"""
Tests for ChemicalNormalizer and EntityResolver chemical category dispatch.

Covers:
  - CAS extraction and checksum validation
  - Abbreviation expansion (Tier 1 and Tier 2)
  - Noise stripping: parentheticals, purity %, grade labels, HS codes
  - Token order preservation (no sorting)
  - Custom (Tier 3) abbreviation loading from JSON
  - CAS short-circuit in EntityResolver
  - Adaptive threshold uses chemical constants (base 90, penalty 15)
"""

import os
import sys
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from api.chemical_normalizer import ChemicalNormalizer, extract_cas
from pipeline.storage.db import init_db
from api.resolver import EntityResolver


# ------------------------------------------------------------------ #
# Fixtures                                                              #
# ------------------------------------------------------------------ #

@pytest.fixture
def n():
    return ChemicalNormalizer()


@pytest.fixture
def con():
    db = init_db(":memory:")
    yield db
    db.close()


# ------------------------------------------------------------------ #
# CAS extraction                                                        #
# ------------------------------------------------------------------ #

class TestCASExtraction:

    def test_standard_format(self, n):
        assert n.extract_cas("Polyethylene [CAS 9002-88-4]") == "9002-88-4"

    def test_hash_format(self, n):
        assert n.extract_cas("Ethylene Glycol CAS# 107-21-1") == "107-21-1"

    def test_registry_format(self, n):
        assert n.extract_cas("Registry: 25038-59-9") == "25038-59-9"

    def test_invalid_checksum_returns_none(self, n):
        # 107-21-2 has wrong check digit (correct is 107-21-1)
        assert n.extract_cas("CAS 107-21-2") is None

    def test_no_cas_returns_none(self, n):
        assert n.extract_cas("Polyethylene Terephthalate Resin") is None

    def test_cas_stripped_from_normalize_output(self, n):
        result = n.normalize("PET Resin [CAS 25038-59-9]")
        assert "25038" not in result
        assert "cas"   not in result


# ------------------------------------------------------------------ #
# Abbreviation expansion                                                #
# ------------------------------------------------------------------ #

class TestAbbreviationExpansion:

    def test_tier1_PET(self, n):
        assert "polyethylene terephthalate" in n.normalize("PET Resin")

    def test_tier1_HDPE(self, n):
        assert "high density polyethylene" in n.normalize("HDPE Granules")

    def test_tier1_PVC(self, n):
        assert "polyvinyl chloride" in n.normalize("PVC Compound")

    def test_tier2_MEG(self, n):
        assert "monoethylene glycol" in n.normalize("MEG (99.9%)")

    def test_tier2_PTA(self, n):
        assert "purified terephthalic acid" in n.normalize("PTA Powder")

    def test_tier2_MDI(self, n):
        assert "methylene diphenyl diisocyanate" in n.normalize("MDI isocyanate")

    def test_case_insensitive_expansion(self, n):
        assert n.normalize("pet resin") == n.normalize("PET Resin")

    def test_longer_abbrev_before_shorter(self, n):
        # LLDPE must not be partially matched as LDPE + residual "L"
        result = n.normalize("LLDPE film")
        assert "linear low density polyethylene" in result
        assert "low density polyethylene" not in result.replace("linear low density polyethylene", "")


# ------------------------------------------------------------------ #
# Noise stripping                                                       #
# ------------------------------------------------------------------ #

class TestNoiseStripping:

    def test_purity_percent_stripped(self, n):
        result = n.normalize("Ethylene Glycol 99.5%")
        assert "99" not in result
        assert "%" not in result

    def test_parenthetical_stripped(self, n):
        result = n.normalize("PET (industrial grade, bale-wrapped)")
        assert "industrial" not in result
        assert "bale"       not in result

    def test_hs_code_stripped(self, n):
        result = n.normalize("Polypropylene HS 3902.10")
        assert "3902" not in result

    def test_grade_label_stripped(self, n):
        result = n.normalize("Methanol Grade A USP")
        assert "grade" not in result
        assert "usp"   not in result

    def test_combined_noise(self, n):
        messy = "HDPE Resin (99% pure, Grade B) [CAS 9002-88-4] HS 3901.20"
        result = n.normalize(messy)
        assert "high density polyethylene" in result
        assert "resin" in result
        # all noise stripped
        for noise in ["99", "%", "grade", "9002", "3901"]:
            assert noise not in result


# ------------------------------------------------------------------ #
# Token order preservation                                              #
# ------------------------------------------------------------------ #

class TestTokenOrderPreservation:

    def test_order_preserved(self, n):
        """Ethylene Oxide and Oxide Ethylene must NOT normalize to the same form."""
        fwd = n.normalize("Ethylene Oxide")
        rev = n.normalize("Oxide Ethylene")
        assert fwd != rev, "Chemical names with different token orders must remain distinct"

    def test_polyethylene_vs_ethylene(self, n):
        a = n.normalize("Polyethylene Terephthalate")
        b = n.normalize("Terephthalate Polyethylene")
        assert a != b

    def test_two_similar_polymers_differ(self, n):
        """HDPE and LDPE expand to different full forms and must stay distinct."""
        assert n.normalize("HDPE") != n.normalize("LDPE")


# ------------------------------------------------------------------ #
# Custom (Tier 3) abbreviations                                         #
# ------------------------------------------------------------------ #

class TestCustomAbbreviations:

    def test_load_from_json(self, n, tmp_path):
        abbrev_file = tmp_path / "custom.json"
        abbrev_file.write_text(json.dumps({"BHET": "bis hydroxyethyl terephthalate"}))
        custom = ChemicalNormalizer.with_custom_abbreviations(str(abbrev_file))
        assert "bis hydroxyethyl terephthalate" in custom.normalize("BHET intermediate")

    def test_custom_overrides_do_not_affect_base(self, n, tmp_path):
        """Loading custom abbreviations must not alter the shared _ABBREV_MAP."""
        abbrev_file = tmp_path / "custom.json"
        abbrev_file.write_text(json.dumps({"TESTX": "test compound x"}))
        ChemicalNormalizer.with_custom_abbreviations(str(abbrev_file))
        # The original normalizer should not know TESTX
        assert "test compound x" not in n.normalize("TESTX")

    def test_invalid_json_raises(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("[1, 2, 3]")  # list, not dict
        with pytest.raises(ValueError, match="JSON object"):
            ChemicalNormalizer.with_custom_abbreviations(str(bad_file))


# ------------------------------------------------------------------ #
# EntityResolver — chemical category dispatch                           #
# ------------------------------------------------------------------ #

class TestResolverChemicalDispatch:

    def _seed(self, con, sup_id, name, country="India"):
        con.execute(
            "INSERT INTO suppliers (id, name, country) VALUES (?, ?, ?)",
            [sup_id, name, country]
        )

    def test_cas_short_circuit(self, con):
        """
        When a valid CAS number is present, resolve() must return match_type='cas_exact'
        and score 100 without touching the fuzzy scanner.
        """
        cas_id = "cas-9002-88-4"
        self._seed(con, cas_id, "Polyethylene (HDPE)", "India")

        resolver = EntityResolver(con, category="chemical")
        result   = resolver.resolve("High Density Polyethylene [CAS 9002-88-4]", country="India")

        assert result["supplier_id"]  == cas_id
        assert result["match_type"]   == "cas_exact"
        assert result["match_score"]  == 100.0
        assert result["is_verified"]  is True

    def test_no_cas_falls_through_to_fuzzy(self, con):
        """Without a CAS number, the chemical resolver still fuzzy-matches on normalized name."""
        self._seed(con, "pet-supplier-1", "Polyethylene Terephthalate", "India")

        resolver = EntityResolver(con, category="chemical")
        result   = resolver.resolve("PET Resin", country="India")

        assert result.get("supplier_id") == "pet-supplier-1"

    def test_chemical_base_threshold_is_90(self, con):
        """_get_adaptive_threshold should use ChemicalNormalizer.BASE_THRESHOLD (90), not 85."""
        self._seed(con, "chem-1", "Polypropylene", "India")
        resolver = EntityResolver(con, category="chemical")
        # Fresh supplier: Laplace neutral rate = 0.5, delta = 0.5 × 15 = 7.5
        # Expected = 90 + 7.5 = 97.5
        expected = ChemicalNormalizer.BASE_THRESHOLD + 0.5 * ChemicalNormalizer.PENALTY_WEIGHT
        assert resolver._get_adaptive_threshold("chem-1") == expected

    def test_textile_resolver_unaffected(self, con):
        """Default (textile) resolver still uses its own BASE_THRESHOLD of 85."""
        self._seed(con, "textile-1", "Welspun India Ltd", "India")
        resolver = EntityResolver(con)  # default category='textile'
        expected = EntityResolver.BASE_THRESHOLD + 0.5 * EntityResolver.PENALTY_WEIGHT
        assert resolver._get_adaptive_threshold("textile-1") == expected


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
