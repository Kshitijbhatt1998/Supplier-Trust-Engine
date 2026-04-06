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

    def __init__(self, con: duckdb.DuckDBPyConnection):
        self.con = con

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
                'is_subsidiary_warning': False
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
                'is_subsidiary_warning': False
            }

        # --- Step 3: Fuzzy Scan (Blocked by Country) ---
        query = "SELECT id, name FROM suppliers"
        params = []
        if country:
            query += " WHERE country = ?"
            params.append(country)
        
        candidates = self.con.execute(query, params).fetchall()
        
        best_match = None
        best_score = 0.0

        input_tokens = set(normalized.split())

        for s_id, s_name in candidates:
            cand_norm = self.normalize(s_name)
            score = fuzz.WRatio(normalized, cand_norm)
            
            if score > best_score:
                # Subsidiary detection (Token Difference check)
                cand_tokens = set(cand_norm.split())
                diff = input_tokens.symmetric_difference(cand_tokens)
                has_location_diff = any(t in self.LOCATION_TOKENS for t in diff)
                
                best_score = score
                best_match = {
                    'supplier_id': s_id,
                    'canonical_name': s_name,
                    'match_score': score,
                    'match_type': 'fuzzy',
                    'is_verified': False,
                    'is_subsidiary_warning': has_location_diff,
                    'low_confidence': score < 85.0
                }

        # --- Step 4: Logic Decision & Registration ---
        THRESHOLD = 85.0
        CLOSE_MISS_THRESHOLD = 75.0
        
        if best_match:
            if best_score >= THRESHOLD:
                # High Confidence: Auto-register/cache
                self._register_alias(name, normalized, best_match['supplier_id'], best_score, False)
                return best_match
            elif best_score >= CLOSE_MISS_THRESHOLD:
                # Close Miss: Surface but DO NOT cache yet (No Ghost Cache)
                logger.info(f"ER Close Miss Surface: '{name}' -> {best_match['supplier_id']} ({best_score:.1f})")
                return best_match

        return {'supplier_id': None, 'match_score': best_score}

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
