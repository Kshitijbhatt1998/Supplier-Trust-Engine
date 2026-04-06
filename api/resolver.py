import re
import unicodedata
import hashlib
from typing import Optional, Tuple
from loguru import logger
import duckdb
from rapidfuzz import fuzz

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
        'enterprises', 'industries', 'mills', 'exim', 'exports', 'imports',
        'textiles', 'fabrics', 'garments', 'apparel', 'intl', 'international'
    }

    def __init__(self, con: duckdb.DuckDBPyConnection):
        self.con = con

    def normalize(self, name: str) -> str:
        """
        Clean and normalize a supplier name for comparison.
        1. Casefold & Transliterate to ASCII
        2. Remove punctuation
        3. Strip common legal/industry suffixes
        4. Token sort alphabetically
        """
        if not name:
            return ""

        # 1. Unicode normalization and ASCII transliteration
        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        name = name.casefold()

        # 2. Remove punctuation and special chars
        name = re.sub(r'[^a-z0-9\s]', ' ', name)

        # 3. Tokenize and strip suffixes
        tokens = [t for t in name.split() if t]
        clean_tokens = [t for t in tokens if t not in self.COMMON_SUFFIXES]
        
        # If stripping everything leaves nothing (e.g. "Exports Ltd"), keep original tokens
        if not clean_tokens:
            clean_tokens = tokens

        # 4. Token sort
        clean_tokens.sort()
        return " ".join(clean_tokens)

    def resolve(self, name: str, country: Optional[str] = None) -> Tuple[Optional[str], float, bool]:
        """
        Resolve a raw name to a canonical supplier_id.
        Returns: (supplier_id, match_score, is_verified)
        """
        normalized = self.normalize(name)
        if not normalized:
            return None, 0.0, False

        # --- Step 1: Fast Path (Exact Alias Lookup) ---
        alias_row = self.con.execute(
            "SELECT canonical_id, match_score, is_verified FROM entity_aliases WHERE alias_normalized = ?",
            [normalized]
        ).fetchone()

        if alias_row and alias_row[0]:
            logger.debug(f"ER Fast Path match: '{name}' -> {alias_row[0]} (Score: {alias_row[1]})")
            return alias_row[0], alias_row[1], bool(alias_row[2])

        # --- Step 2: Exact Name Match in Suppliers ---
        # (Handling the case where the canonical name itself matches the input)
        supplier_row = self.con.execute(
            "SELECT id FROM suppliers WHERE lower(name) = ?",
            [name.lower()]
        ).fetchone()
        
        if supplier_row:
            self._register_alias(name, normalized, supplier_row[0], 100.0, True)
            return supplier_row[0], 100.0, True

        # --- Step 3: Fuzzy Scan (Blocked by Country) ---
        # Fetching names from the same country to reduce search space
        query = "SELECT id, name FROM suppliers"
        params = []
        if country:
            query += " WHERE country = ?"
            params.append(country)
        
        candidates = self.con.execute(query, params).fetchall()
        
        best_match_id = None
        best_score = 0.0

        for s_id, s_name in candidates:
            # We use WRatio as it's more robust to length differences and substring matches
            score = fuzz.WRatio(normalized, self.normalize(s_name))
            if score > best_score:
                best_score = score
                best_match_id = s_id

        # --- Step 4: Logic Decision & Registration ---
        THRESHOLD = 85.0
        CLOSE_MISS_THRESHOLD = 75.0

        if best_score >= THRESHOLD:
            logger.info(f"ER Fuzzy Match: '{name}' -> {best_match_id} (Score: {best_score:.1f})")
            self._register_alias(name, normalized, best_match_id, best_score, False)
            return best_match_id, best_score, False
        
        elif best_score >= CLOSE_MISS_THRESHOLD:
            logger.warning(f"ER Close Miss: '{name}' matched {best_match_id} with score {best_score:.1f}")
            # We don't cache close misses as "resolutions" but we could log them for audit
            return None, best_score, False

        return None, best_score, False

    def _register_alias(self, raw_name: str, normalized: str, canonical_id: str, score: float, verified: bool):
        """Cache a resolution in the entity_aliases table."""
        alias_id = hashlib.sha256(raw_name.lower().encode()).hexdigest()[:20]
        try:
            self.con.execute("""
                INSERT INTO entity_aliases (id, alias_name, alias_normalized, canonical_id, match_score, is_verified)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO UPDATE SET
                    canonical_id = excluded.canonical_id,
                    match_score = excluded.match_score,
                    is_verified = excluded.is_verified,
                    resolved_at = NOW()
            """, [alias_id, raw_name, normalized, canonical_id, score, verified])
        except Exception as e:
            logger.error(f"Failed to register alias: {e}")
