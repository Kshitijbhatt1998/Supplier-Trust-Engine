"""
ChemicalNormalizer — supplier name normalization for chemical/polymer trade manifests.

Key differences from the textile EntityResolver.normalize():
  - Token sorting DISABLED: word order is semantic in chemical nomenclature.
  - CAS number extraction + checksum validation provides a definitive canonical key,
    short-circuiting fuzzy matching entirely when present.
  - Abbreviation expansion runs BEFORE fuzzy scoring so "PET" and
    "Polyethylene Terephthalate" share a common normalized form.
  - Noise stripping targets chemical-specific patterns (purity %, grades, HS codes)
    rather than corporate-suffix stripping.
  - Higher base threshold (90) and penalty weight (15) vs. textile (85/12).
"""

import re
import json
import unicodedata
from pathlib import Path
from typing import Optional


# ------------------------------------------------------------------ #
# Abbreviation dictionary                                               #
# ------------------------------------------------------------------ #

# Tier 1 — High-certainty, globally unambiguous polymer/chemical codes.
# Source: IUPAC / ISO / common trade practice.
_TIER1: dict[str, str] = {
    "PET":   "polyethylene terephthalate",
    "PVC":   "polyvinyl chloride",
    "HDPE":  "high density polyethylene",
    "LDPE":  "low density polyethylene",
    "LLDPE": "linear low density polyethylene",
    "MDPE":  "medium density polyethylene",
    "PP":    "polypropylene",
    "PS":    "polystyrene",
    "ABS":   "acrylonitrile butadiene styrene",
    "PMMA":  "polymethyl methacrylate",
    "PC":    "polycarbonate",
    "PA":    "polyamide",
    "PA6":   "polyamide 6",
    "PA66":  "polyamide 6 6",
    "POM":   "polyoxymethylene",
    "PTFE":  "polytetrafluoroethylene",
    "PU":    "polyurethane",
    "PUR":   "polyurethane",
    "EPS":   "expanded polystyrene",
    "XPS":   "extruded polystyrene",
    "EVA":   "ethylene vinyl acetate",
    "EVOH":  "ethylene vinyl alcohol",
    "PBT":   "polybutylene terephthalate",
    "PPE":   "polyphenylene ether",
    "PPS":   "polyphenylene sulfide",
    "PEEK":  "polyether ether ketone",
    "TPU":   "thermoplastic polyurethane",
    "TPE":   "thermoplastic elastomer",
    "TPR":   "thermoplastic rubber",
    "SBR":   "styrene butadiene rubber",
    "NBR":   "nitrile butadiene rubber",
    "EPDM":  "ethylene propylene diene monomer",
    "NR":    "natural rubber",
}

# Tier 2 — Industry-specific, high-confidence within chemicals/petrochemicals.
# Source: ICIS, Platts, CHEM trade nomenclature.
_TIER2: dict[str, str] = {
    "MEG":   "monoethylene glycol",
    "DEG":   "diethylene glycol",
    "TEG":   "triethylene glycol",
    "EG":    "ethylene glycol",
    "PG":    "propylene glycol",
    "PTA":   "purified terephthalic acid",
    "DMT":   "dimethyl terephthalate",
    "MAN":   "maleic anhydride",
    "PA_A":  "phthalic anhydride",   # PA is ambiguous (polyamide vs phthalic anhydride)
    "IPA":   "isophthalic acid",
    "TPA":   "terephthalic acid",
    "EDC":   "ethylene dichloride",
    "VCM":   "vinyl chloride monomer",
    "ACN":   "acrylonitrile",
    "AN":    "acrylonitrile",
    "BD":    "butadiene",
    "SM":    "styrene monomer",
    "BPA":   "bisphenol a",
    "ECH":   "epichlorohydrin",
    "TDI":   "toluene diisocyanate",
    "MDI":   "methylene diphenyl diisocyanate",
    "MDA":   "methylene dianiline",
    "CAPRo": "caprolactam",
    "AA":    "adipic acid",
    "HDA":   "hexamethylene diamine",
    "HMDA":  "hexamethylene diamine",
    "CPL":   "caprolactam",
    "LAB":   "linear alkylbenzene",
    "LABSA": "linear alkylbenzene sulfonic acid",
    "FAME":  "fatty acid methyl ester",
    "DOP":   "dioctyl phthalate",
    "DINP":  "diisononyl phthalate",
    "DEHP":  "diethylhexyl phthalate",
}

# Merged lookup: keys uppercased for case-insensitive matching at expand time.
_ABBREV_MAP: dict[str, str] = {k.upper(): v for k, v in {**_TIER1, **_TIER2}.items()}


# ------------------------------------------------------------------ #
# CAS number utilities                                                  #
# ------------------------------------------------------------------ #

# Matches: CAS 9002-88-4 | CAS# 107-21-1 | Registry: [9002-86-2]
_CAS_PATTERN = re.compile(
    r"(?:CAS|CAS#|Registry)\s?[:#]?\s?\[?(\d{2,7}-\d{2}-\d)\]?",
    re.IGNORECASE,
)

def extract_cas(text: str) -> Optional[str]:
    """
    Extract and validate a CAS Registry Number from raw text.

    CAS checksum: sum(digit_i × position_from_right_starting_at_1) mod 10
    must equal the check digit (last single digit after the final dash).
    Returns the canonical "NNNNNN-NN-N" string, or None if not found/invalid.
    """
    m = _CAS_PATTERN.search(text)
    if not m:
        return None
    candidate = m.group(1)
    digits = candidate.replace("-", "")
    # Last digit is the check digit; preceding digits form the payload
    check_digit = int(digits[-1])
    payload     = digits[:-1]
    total = sum(int(d) * (i + 1) for i, d in enumerate(reversed(payload)))
    return candidate if (total % 10) == check_digit else None


def cas_to_canonical_id(cas: str) -> str:
    """Convert a validated CAS string to a stable supplier/entity canonical ID."""
    return f"cas-{cas}"


# ------------------------------------------------------------------ #
# Noise patterns                                                        #
# ------------------------------------------------------------------ #

# Strips parenthetical asides: (99.5% purity), (industrial grade), (bale-wrapped)
_PARENS_NOISE   = re.compile(r"\([^)]*\)")
# Strips standalone percentage clusters: 99.5%, ≥ 98%, >99%
_PERCENT_NOISE  = re.compile(r"[≥><=]?\s*\d+\.?\d*\s*%")
# Strips HS/HTS code annotations: HS 3902.10, HTS:3902.10.00
_HS_NOISE       = re.compile(r"\b(?:HS|HTS)[:#\s]*\d{4}[\d.]*", re.IGNORECASE)
# Strips unit/grade suffixes: "Grade A", "Type II", "Class 1", "USP", "NF", "BP", "EP"
_GRADE_NOISE    = re.compile(
    r"\b(?:Grade|Type|Class|Form)\s+[A-Z0-9]+\b"
    r"|\b(?:USP|NF|BP|EP|FCC|ACS|Reagent|Technical|Industrial|Commercial)\b",
    re.IGNORECASE,
)
# THE ROLE SHIELD: Strips common "Care Of" clusters to find the true manufacturer.
_ROLE_NOISE = re.compile(
    r"\b(?:C/O|C-O|CARE\s+OF|VIA|BY)\b.*$", 
    re.IGNORECASE
)


# ------------------------------------------------------------------ #
# ChemicalNormalizer                                                    #
# ------------------------------------------------------------------ #

class ChemicalNormalizer:
    """
    Normalizer for chemical/polymer supplier names and product descriptors.

    Usage:
        n = ChemicalNormalizer()
        n.normalize("PET Resin (99.5% purity, Grade A) [CAS 25038-59-9]")
        # → "polyethylene terephthalate resin"

        cas = n.extract_cas("Polyethylene [CAS 9002-88-4]")
        # → "9002-88-4"

    Custom (Tier 3) abbreviations can be loaded from a JSON file:
        n = ChemicalNormalizer.with_custom_abbreviations("/path/to/abbrev.json")
    """

    BASE_THRESHOLD   = 90.0
    MAX_THRESHOLD    = 99.0
    PENALTY_WEIGHT   = 15.0

    def __init__(self, extra_abbreviations: Optional[dict[str, str]] = None):
        self._abbrev = dict(_ABBREV_MAP)
        if extra_abbreviations:
            self._abbrev.update({k.upper(): v for k, v in extra_abbreviations.items()})

    @classmethod
    def with_custom_abbreviations(cls, path: str) -> "ChemicalNormalizer":
        """
        Load Tier 3 (deployment-specific) abbreviations from a JSON file.

        Expected format:
            { "DWR": "durable water repellent finish", "BHET": "bis hydroxyethyl terephthalate" }
        """
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"Abbreviation file must be a JSON object, got {type(data)}")
        return cls(extra_abbreviations=data)

    # ---------------------------------------------------------------- #
    # Public API                                                         #
    # ---------------------------------------------------------------- #

    def extract_cas(self, text: str) -> Optional[str]:
        """Extract and validate a CAS number from raw text. Returns None if absent/invalid."""
        return extract_cas(text)

    def normalize(self, name: str) -> str:
        """
        Produce a canonical comparison form for a chemical name.
        """
        if not name:
            return ""

        text = name

        # 1. Noise stripping (including Role Shield)
        text = _ROLE_NOISE.sub(" ", text)
        text = _PARENS_NOISE.sub(" ", text)
        text = _PERCENT_NOISE.sub(" ", text)
        text = _HS_NOISE.sub(" ", text)
        text = _GRADE_NOISE.sub(" ", text)
        text = _CAS_PATTERN.sub(" ", text)

        # 2. Abbreviation expansion
        for abbr in sorted(self._abbrev, key=len, reverse=True):
            pattern = re.compile(r"\b" + re.escape(abbr) + r"\b", re.IGNORECASE)
            if pattern.search(text):
                text = pattern.sub(self._abbrev[abbr], text)

        # 3. Casefold + ASCII transliteration
        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
        text = text.casefold()

        # 4. Remove residual punctuation (keep hyphens within compound names)
        text = re.sub(r"[^a-z0-9\s\-]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        return text

    def normalize_for_cas(self, name: str) -> tuple[Optional[str], str, bool]:
        """
        Attempt CAS extraction first; fall back to normalize().
        
        Returns (cas_id, normalized_name, is_surrogate)
        """
        is_surrogate = bool(_ROLE_NOISE.search(name))
        cas = self.extract_cas(name)
        normalized = self.normalize(name)
        return (cas_to_canonical_id(cas) if cas else None, normalized, is_surrogate)
