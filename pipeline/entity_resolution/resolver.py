"""
Entity Resolution Layer — Supplier Trust Engine
================================================

Problem: the same physical supplier appears under different name forms
across data sources:

  ImportYeti  : "Welspun India Ltd"
  Bill of Lading: "WELSPUN INDIA LIMITED"
  IndiaMart   : "Welspun"

Without resolution each variant creates a duplicate supplier record,
splitting the trust signal across phantom entities and skewing scores.

Algorithm
---------
1. Normalize   — casefold, strip legal suffixes, strip punctuation,
                 token-sort (so "India Welspun" == "Welspun India")
2. Fast path   — exact lookup in entity_aliases on alias_normalized (O(1))
3. Candidate   — load suppliers blocked by country to avoid O(n²) global scan
   blocking
4. Score       — combined fuzzywuzzy metric:
                   score = 0.65 * token_sort_ratio + 0.35 * partial_ratio
                 token_sort_ratio handles reordering
                 partial_ratio handles abbreviations ("Welspun" < "Welspun India Ltd")
5. Decision    — best_score >= threshold (default 85) → link to canonical_id
6. Registration— every seen alias is persisted so the next lookup is O(1)
"""

import re
import hashlib
from dataclasses import dataclass
from typing import Optional, List, Tuple

import duckdb
from rapidfuzz import fuzz
from loguru import logger


# ------------------------------------------------------------------ #
# Normalization                                                         #
# ------------------------------------------------------------------ #

_LEGAL_RE = re.compile(
    r"\b("
    r"pvt|private|ltd|limited|llc|llp|inc|corp|corporation|"
    r"co|company|gmbh|s\.?a\.?|b\.?v\.?|n\.?v\.?|ag|oy|ab|a\.?s\.?|pty|plc|"
    r"holdings|group|intl|international|industries|industry|"
    r"exports|export|import|imports|manufacturing|mfg|"
    r"textile|textiles|fabric|fabrics|garment|garments|"
    r"enterprises|enterprise|trading|traders|trader"
    r")\b",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[^a-z0-9\s]")
_SPACE_RE = re.compile(r"\s+")


def normalize(name: str) -> str:
    """
    Canonical form used for all similarity comparisons.

    >>> normalize("WELSPUN INDIA LIMITED")
    'india welspun'
    >>> normalize("Welspun India Ltd")
    'india welspun'
    >>> normalize("Welspun")
    'welspun'
    >>> normalize("Shahi Exports Pvt. Ltd.")
    'shahi'
    """
    s = name.lower()
    s = _LEGAL_RE.sub(" ", s)
    s = _PUNCT_RE.sub(" ", s)
    tokens = sorted(t for t in s.split() if len(t) > 1)  # token-sort, drop 1-char noise
    s = " ".join(tokens)
    return _SPACE_RE.sub(" ", s).strip()


def _slugify(name: str) -> str:
    """Stable DuckDB primary key derived from a company name."""
    clean = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return clean[:80]


def _alias_id(raw_name: str) -> str:
    return hashlib.sha256(raw_name.lower().strip().encode()).hexdigest()[:20]


# ------------------------------------------------------------------ #
# Result type                                                          #
# ------------------------------------------------------------------ #

@dataclass
class ResolveResult:
    canonical_id: str
    canonical_name: str
    is_new: bool      # True → brand-new record; False → matched existing
    score: float      # 100 alias/exact, 85–99 fuzzy match, 0 for new
    source: str


# ------------------------------------------------------------------ #
# Entity Resolver                                                      #
# ------------------------------------------------------------------ #

class EntityResolver:
    """
    Resolves a raw supplier name (+ optional country) to a canonical supplier_id.

    Example::

        con = init_db()
        resolver = EntityResolver(con, threshold=85)

        r = resolver.resolve("WELSPUN INDIA LIMITED", country="India", source="bol")
        # ResolveResult(canonical_id='welspun-india-ltd',
        #               canonical_name='Welspun India Ltd',
        #               is_new=False, score=100.0, source='bol')

        r = resolver.resolve("Welspun", country="India", source="indiamart")
        # ResolveResult(canonical_id='welspun-india-ltd', ..., score=87.3, ...)
    """

    def __init__(self, con: duckdb.DuckDBPyConnection, threshold: int = 85):
        self.con = con
        self.threshold = threshold

    # ---------------------------------------------------------------- #
    # Public API                                                         #
    # ---------------------------------------------------------------- #

    def resolve(
        self,
        raw_name: str,
        country: Optional[str] = None,
        source: str = "unknown",
    ) -> ResolveResult:
        """
        Resolve a raw company name to a canonical supplier entity.

        Resolution order:
          1. Exact alias lookup (fast path, indexed)
          2. Fuzzy scan of suppliers table, blocked by country
          3. New entity — slug-based ID, alias registered for next time
        """
        raw_name = raw_name.strip()
        if not raw_name:
            raise ValueError("raw_name cannot be empty")

        norm = normalize(raw_name)

        # -- 1. Fast path: alias table exact match -----------------------
        row = self.con.execute(
            "SELECT canonical_id FROM entity_aliases WHERE alias_normalized = ?",
            [norm],
        ).fetchone()
        if row:
            canonical_id = row[0]
            canonical_name = self._get_name(canonical_id)
            logger.debug(f"[ER] alias hit  '{raw_name}' → '{canonical_name}'")
            # re-register in case source is new (DO NOTHING on conflict)
            self._register_alias(raw_name, norm, canonical_id, source, 100.0)
            return ResolveResult(canonical_id, canonical_name, False, 100.0, source)

        # -- 2. Fuzzy scan, blocked by country ---------------------------
        candidates = self._load_candidates(country)
        if candidates:
            best_id, best_name, best_score = self._best_match(norm, candidates)
            if best_score >= self.threshold:
                logger.info(
                    f"[ER] fuzzy match '{raw_name}' → '{best_name}' "
                    f"(score={best_score:.1f}, via={source})"
                )
                self._register_alias(raw_name, norm, best_id, source, best_score)
                return ResolveResult(best_id, best_name, False, best_score, source)

        # -- 3. New entity -----------------------------------------------
        new_id = _slugify(raw_name)
        # Slug collision guard: if the slug already exists for a *different* entity
        # (e.g. "Welspun" colliding with "welspun-india-ltd" is fine, but two
        # distinct companies happening to slugify identically must not merge)
        existing = self.con.execute(
            "SELECT id FROM suppliers WHERE id = ?", [new_id]
        ).fetchone()
        if existing:
            new_id = f"{new_id}-{_alias_id(raw_name)[:6]}"

        logger.info(f"[ER] new entity  '{raw_name}' → id='{new_id}' (source={source})")
        self._register_alias(raw_name, norm, new_id, source, 0.0)
        return ResolveResult(new_id, raw_name, True, 0.0, source)

    # ---------------------------------------------------------------- #
    # Internals                                                          #
    # ---------------------------------------------------------------- #

    def _get_name(self, supplier_id: str) -> str:
        row = self.con.execute(
            "SELECT name FROM suppliers WHERE id = ?", [supplier_id]
        ).fetchone()
        return row[0] if row else supplier_id

    def _load_candidates(
        self, country: Optional[str]
    ) -> List[Tuple[str, str, str]]:
        """Return (id, name, normalized_name) tuples.

        Blocked by country when possible — this keeps comparisons O(suppliers/country)
        rather than O(total suppliers). For a country like India with ~5 k suppliers,
        each resolve() does at most 5 k comparisons which is ~10 ms.
        """
        if country:
            rows = self.con.execute(
                "SELECT id, name FROM suppliers WHERE country = ? LIMIT 5000",
                [country],
            ).fetchall()
        else:
            # No country hint: scan first 2000 alphabetically as a best-effort
            rows = self.con.execute(
                "SELECT id, name FROM suppliers ORDER BY name LIMIT 2000"
            ).fetchall()
        return [(r[0], r[1], normalize(r[1])) for r in rows]

    def _best_match(
        self, norm_input: str, candidates: List[Tuple[str, str, str]]
    ) -> Tuple[str, str, float]:
        """Score every candidate and return the single best (id, name, score)."""
        best_id, best_name, best_score = "", "", 0.0

        for cid, cname, cnorm in candidates:
            # token_sort_ratio: handles reordering ("India Welspun" == "Welspun India")
            # token_set_ratio:  handles subset/abbreviation ("Welspun" ≈ "Welspun India Ltd")
            #   token_set builds: sorted(intersection), sorted(a), sorted(b)
            #   and takes max pairwise ratio — so a short name that is a token subset
            #   of the longer canonical name scores 100.
            ts = fuzz.token_sort_ratio(norm_input, cnorm)
            tset = fuzz.token_set_ratio(norm_input, cnorm)
            score = max(ts, tset)
            if score > best_score:
                best_score, best_id, best_name = score, cid, cname
            if best_score >= 100:
                break  # can't do better

        return best_id, best_name, best_score

    def _register_alias(
        self,
        raw_name: str,
        norm: str,
        canonical_id: str,
        source: str,
        score: float,
    ) -> None:
        """Persist alias → canonical mapping. Idempotent (ON CONFLICT DO NOTHING)."""
        aid = _alias_id(raw_name)
        self.con.execute(
            """
            INSERT INTO entity_aliases
                (id, alias_name, alias_normalized, canonical_id, source, match_score)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO NOTHING
            """,
            [aid, raw_name, norm, canonical_id, source, score],
        )


# ------------------------------------------------------------------ #
# Convenience wrapper used by scrapers                                 #
# ------------------------------------------------------------------ #

def resolve_and_upsert(
    con: duckdb.DuckDBPyConnection,
    supplier: dict,
    threshold: int = 85,
) -> ResolveResult:
    """
    Primary entry point for all scrapers.

    Resolves the supplier name to a canonical entity, then upserts the
    record under that canonical ID. Scrapers should call this instead of
    upsert_supplier() directly.

    If a fuzzy match is found, the incoming stats (shipment_count, etc.)
    are merged into the existing canonical record via upsert_supplier's
    ON CONFLICT UPDATE path — enriching rather than forking the entity.

    Args:
        con:       Active DuckDB connection (from init_db()).
        supplier:  Dict with at least 'name'. May include 'country',
                   'source', 'shipment_count', etc.
        threshold: Similarity threshold (0–100). Default 85 works well
                   for textile supplier names; lower to 75 for very short
                   names or highly abbreviated data sources.

    Returns:
        ResolveResult with canonical_id, canonical_name, is_new, score.
    """
    from pipeline.storage.db import upsert_supplier  # avoid circular import at module level

    resolver = EntityResolver(con, threshold)
    result = resolver.resolve(
        raw_name=supplier["name"],
        country=supplier.get("country"),
        source=supplier.get("source", "unknown"),
    )

    # Always upsert under the canonical ID.
    # - is_new=True  → INSERT (upsert_supplier inserts fresh)
    # - is_new=False → UPDATE scalars on the canonical record (merges stats)
    upsert_supplier(con, {**supplier, "id": result.canonical_id})

    return result
