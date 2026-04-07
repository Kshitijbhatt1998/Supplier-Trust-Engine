import re
import unicodedata
import hashlib
from typing import Optional
from loguru import logger
import duckdb
from rapidfuzz import fuzz

from api.chemical_normalizer import ChemicalNormalizer

class EntityResolver:
    """
    Handles supplier name normalization and fuzzy resolution to canonical IDs.
    
    Includes:
    - ASCII transliteration (é -> e)
    - Legal suffix stripping (Ltd, Co, Pvt Ltd, Exim, etc.)
    - Token sorting for reordering resilience
    - Fast path (Exact normalized lookup)
    - Fuzzy scan (WRatio, blocked by country)
    """

    # Common legal and textile-specific suffixes to strip for normalization
    COMMON_SUFFIXES = {
        'ltd', 'limited', 'pvt', 'private', 'co', 'company', 'corp', 'corporation',
        'inc', 'incorporated', 'llc', 'plc', 'gmbh', 'sa', 'srl', 'aps', 'as',
        'mills', 'exim', 'intl', 'international'
    }

    # Brand terms that should NOT be stripped if they are the only core identity
    # e.g. "Limited Brands", "Enterprises Inc"
    PROTECTED_TERMS = {
        'limited', 'brands', 'enterprises', 'industries', 'mill', 'mills', 'general', 'global'
    }

    # Location tokens for subsidiary detection
    LOCATION_TOKENS = {
        'gujarat', 'vapi', 'delhi', 'mumbai', 'surat', 'tirupur', 'dhaka', 'istanbul', 'bursa'
    }

    # Threshold bounds and penalty knob (configurable per industry/deployment)
    BASE_THRESHOLD    = 85.0
    MAX_THRESHOLD     = 97.0   # hard ceiling — never demand a perfect match
    PENALTY_WEIGHT    = 12.0   # points added at rejection_rate=1.0; tune per category

    def __init__(self, con: duckdb.DuckDBPyConnection, category: str = "textile"):
        self.con      = con
        self.category = category
        self._chem    = ChemicalNormalizer() if category == "chemical" else None

    def normalize(self, name: str) -> str:
        """
        Clean and normalize a supplier name for comparison.
        1. Casefold & Transliterate
        2. Remove punctuation
        3. Strip suffixes with protection for brand terms
        4. Token sort
        """
        if not name:
            return ""

        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        name = name.casefold()
        name = re.sub(r'[^a-z0-9\s]', ' ', name)

        tokens = [t for t in name.split() if t]
        
        # Suffix stripping with "Protected Terms" check
        # If a term is on both lists, we only strip it if it's accompanied by other core tokens
        clean_tokens = []
        core_count = 0
        
        for t in tokens:
            if t in self.COMMON_SUFFIXES:
                if t in self.PROTECTED_TERMS:
                    # It's a brand term, keep it for now but don't count as 'unique' core
                    clean_tokens.append(t)
                else:
                    # Safe to strip
                    continue
            else:
                clean_tokens.append(t)
                core_count += 1

        # If stripping leaves only protected terms without any other core tokens,
        # we revert to the full token set to avoid matching generic labels
        if core_count == 0:
            clean_tokens = tokens

        clean_tokens.sort()
        return " ".join(clean_tokens)

    def _constants(self) -> tuple[float, float, float]:
        """Return (BASE_THRESHOLD, MAX_THRESHOLD, PENALTY_WEIGHT) for current category."""
        if self._chem:
            return self._chem.BASE_THRESHOLD, self._chem.MAX_THRESHOLD, self._chem.PENALTY_WEIGHT
        return self.BASE_THRESHOLD, self.MAX_THRESHOLD, self.PENALTY_WEIGHT

    def _get_adaptive_threshold(self, canonical_id: str) -> float:
        """
        Return a per-supplier fuzzy threshold raised by accumulated admin rejections.

        Uses Laplace smoothing so a single accidental rejection can't spike the
        threshold to the ceiling.  Formula (see resolver_config view):
            rate  = (rejections + 1) / (rejections + verifications + 2)
            delta = rate × PENALTY_WEIGHT
            floor((BASE + delta) to MAX_THRESHOLD)

        If is_verified aliases already outnumber rejections, rate < 0.5 and the
        penalty is less than PENALTY_WEIGHT/2 — effectively a "clean slate" bonus.
        """
        base, max_t, penalty = self._constants()

        row = self.con.execute(
            "SELECT laplace_rejection_rate FROM resolver_config WHERE canonical_id = ?",
            [canonical_id]
        ).fetchone()

        if row is None:
            return base

        rate  = float(row[0])
        delta = rate * penalty
        return min(base + delta, max_t)

    def resolve(self, name: str, country: Optional[str] = None) -> dict:
        """
        Resolve name to a canonical supplier_id.
        Returns: {
            'supplier_id': str,
            'canonical_name': str,
            'match_score': float,
            'match_type': 'exact' | 'fuzzy' | 'alias',
            'is_verified': bool,
            'is_subsidiary_warning': bool
        }
        """
        # --- CAS short-circuit (chemical category only) ---
        is_surrogate = False
        if self._chem:
            cas_id, normalized, is_surrogate = self._chem.normalize_for_cas(name)
            if cas_id:
                # CAS number present and checksum-valid: bypass fuzzy entirely
                row = self.con.execute(
                    "SELECT id, name FROM suppliers WHERE id = ?", [cas_id]
                ).fetchone()
                if row:
                    self._register_alias(name, normalized, cas_id, 100.0, True)
                    return {
                        'supplier_id':          cas_id,
                        'canonical_name':       row[1],
                        'match_score':          100.0,
                        'match_type':           'cas_exact',
                        'is_verified':          True,
                        'is_subsidiary_warning': False,
                        'is_role_warning':       is_surrogate,
                        'low_confidence':       False,
                    }
        else:
            normalized = self.normalize(name)

        if not normalized:
            return {'supplier_id': None, 'match_score': 0.0}

        # --- Step 1: Fast Path (Exact Alias Lookup) ---
        alias_row = self.con.execute(
            "SELECT canonical_id, match_score, is_verified FROM entity_aliases WHERE alias_normalized = ?",
            [normalized]
        ).fetchone()

        if alias_row and alias_row[0]:
            name_row = self.con.execute("SELECT name FROM suppliers WHERE id = ?", [alias_row[0]]).fetchone()
            return {
                'supplier_id': alias_row[0],
                'canonical_name': name_row[0] if name_row else alias_row[0],
                'match_score': alias_row[1],
                'match_type': 'alias',
                'is_verified': bool(alias_row[2]),
                'is_subsidiary_warning': False,
                'is_role_warning': is_surrogate
            }

        # --- Step 2: Exact Name Match ---
        supplier_row = self.con.execute(
            "SELECT id, name FROM suppliers WHERE lower(name) = ?",
            [name.lower()]
        ).fetchone()
        
        if supplier_row:
            self._register_alias(name, normalized, supplier_row[0], 100.0, True)
            return {
                'supplier_id': supplier_row[0],
                'canonical_name': supplier_row[1],
                'match_score': 100.0,
                'match_type': 'exact',
                'is_verified': True,
                'is_subsidiary_warning': False,
                'is_role_warning': is_surrogate
            }

        # --- Step 3: Fuzzy Scan (Blocked by Country & Negative Feedback) ---
        rejection_rows = self.con.execute(
            "SELECT canonical_id FROM entity_rejections WHERE alias_normalized = ?",
            [normalized]
        ).fetchall()
        rejections = {row[0] for row in rejection_rows}

        query = "SELECT id, name FROM suppliers"
        params = []
        if country:
            query += " WHERE country = ?"
            params.append(country)
        
        candidates = self.con.execute(query, params).fetchall()
        
        best_match = None
        best_score = 0.0

        for s_id, s_name in candidates:
            # Skip previously rejected pairs
            if s_id in rejections:
                continue

            cand_norm = self.normalize(s_name)
            score = fuzz.WRatio(normalized, cand_norm)

            if score > best_score:
                # Subsidiary detection (Token Difference)
                diff = set(normalized.split()).symmetric_difference(set(cand_norm.split()))
                is_sub = any(t in self.LOCATION_TOKENS for t in diff)

                best_score = score
                best_match = {
                    'supplier_id': s_id,
                    'canonical_name': s_name,
                    'match_score': score,
                    'is_verified': False,
                    'is_subsidiary_warning': is_sub,
                    'is_role_warning': is_surrogate
                }

        # --- Step 4: Logic Decision & Registration ---
        # MIN_THRESHOLD is a static floor; the upper bar is per-supplier adaptive.
        MIN_THRESHOLD = 75.0

        if best_match and best_score >= MIN_THRESHOLD:
            threshold = self._get_adaptive_threshold(best_match['supplier_id'])
            best_match['low_confidence'] = best_score < threshold

            if best_score >= threshold:
                # High confidence: auto-register in alias cache
                self._register_alias(name, normalized, best_match['supplier_id'], best_score, False)
            else:
                logger.debug(
                    f"Fuzzy match '{name}' → '{best_match['canonical_name']}' "
                    f"score={best_score:.1f} below adaptive threshold={threshold:.1f} "
                    f"(held for admin review)"
                )
            return best_match

        return {'supplier_id': None, 'match_score': best_score}

    def _register_alias(self, raw_name: str, normalized: str, canonical_id: str, score: float, verified: bool):
        """Cache a resolution in the entity_aliases table."""
        alias_id = hashlib.sha256(raw_name.lower().encode()).hexdigest()[:20]
        try:
            self.con.execute("""
                INSERT INTO entity_aliases
                    (id, alias_name, alias_normalized, canonical_id, match_score, is_verified, category)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO UPDATE SET
                    canonical_id = excluded.canonical_id,
                    match_score  = excluded.match_score,
                    is_verified  = excluded.is_verified,
                    category     = excluded.category,
                    resolved_at  = NOW()
            """, [alias_id, raw_name, normalized, canonical_id, score, verified, self.category])
        except Exception as e:
            logger.error(f"Failed to register alias: {e}")
