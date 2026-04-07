"""
Tests for the Role Shield — C/O stripping, surrogate flagging, and
the entity_rejections pre-seeding that blocks trader-manufacturer pollution.

Covers:
  1. ChemicalNormalizer strips C/O, VIA, BY clusters from the alias name.
  2. normalize_for_cas() flags is_surrogate=True when role noise was present.
  3. EntityResolver.resolve() maps "SABIC C/O XYZ LOGISTICS" to the SABIC
     canonical and returns is_role_warning=True.
  4. A pre-seeded entity_rejection for "XYZ LOGISTICS" → "sabic-global"
     means a direct resolve of that trader returns no match.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from api.chemical_normalizer import ChemicalNormalizer
from api.resolver import EntityResolver
from pipeline.storage.db import init_db


# ------------------------------------------------------------------ #
# Fixtures                                                              #
# ------------------------------------------------------------------ #

@pytest.fixture
def norm():
    return ChemicalNormalizer()


@pytest.fixture
def con():
    db = init_db(":memory:")
    # Seed SABIC as a canonical chemical supplier
    db.execute(
        "INSERT INTO suppliers (id, name, country, category) VALUES ('sabic-global', 'SABIC Innovative Plastics', 'Saudi Arabia', 'chemical')"
    )
    db.execute(
        "INSERT INTO trust_scores (supplier_id, trust_score, risk_label) VALUES ('sabic-global', 98, 0)"
    )
    yield db
    db.close()


# ------------------------------------------------------------------ #
# 1. Normalizer: role noise is stripped                                 #
# ------------------------------------------------------------------ #

class TestRoleNoiseStripping:

    def test_care_of_stripped(self, norm):
        result = norm.normalize("SABIC C/O XYZ LOGISTICS")
        assert "c/o"      not in result.lower()
        assert "logistics" not in result.lower()
        assert "sabic"    in result.lower()

    def test_via_stripped(self, norm):
        result = norm.normalize("Reliance Industries VIA Mumbai Port Agents")
        assert "via"      not in result.lower()
        assert "mumbai"   not in result.lower()
        assert "reliance" in result.lower()

    def test_by_stripped(self, norm):
        result = norm.normalize("ExxonMobil Chemical BY ABC Trading Co")
        assert " by "     not in f" {result.lower()} "
        assert "abc"      not in result.lower()
        assert "exxon"    in result.lower()

    def test_care_of_long_form_stripped(self, norm):
        result = norm.normalize("HDPE CARE OF MITSUBISHI CORP")
        assert "care"       not in result.lower()
        assert "mitsubishi" not in result.lower()

    def test_clean_name_unchanged(self, norm):
        """A name with no role cluster must not be modified by role stripping."""
        clean = norm.normalize("Polyethylene Terephthalate Resin")
        noisy = norm.normalize("Polyethylene Terephthalate Resin VIA XYZ TRADER")
        # Core tokens must survive; surrogate tokens must be gone
        assert "polyethylene" in noisy
        assert "xyz"          not in noisy
        # Clean version must be identical to the role-stripped version
        assert clean == noisy


# ------------------------------------------------------------------ #
# 2. normalize_for_cas: surrogate flag                                 #
# ------------------------------------------------------------------ #

class TestSurrogateFlag:

    def test_surrogate_true_when_role_noise_present(self, norm):
        _, _, is_surrogate = norm.normalize_for_cas("SABIC C/O XYZ LOGISTICS")
        assert is_surrogate is True

    def test_surrogate_false_for_clean_name(self, norm):
        _, _, is_surrogate = norm.normalize_for_cas("SABIC Innovative Plastics")
        assert is_surrogate is False

    def test_surrogate_true_via_pattern(self, norm):
        _, _, is_surrogate = norm.normalize_for_cas("Reliance Industries VIA Port Agent")
        assert is_surrogate is True

    def test_surrogate_does_not_affect_normalization(self, norm):
        _, normalized, _ = norm.normalize_for_cas("SABIC C/O XYZ LOGISTICS")
        assert "sabic" in normalized
        assert "logistics" not in normalized


# ------------------------------------------------------------------ #
# 3. EntityResolver: surrogate resolves to manufacturer + warning      #
# ------------------------------------------------------------------ #

class TestResolverRoleWarning:

    def test_co_string_resolves_to_canonical_with_warning(self, con):
        """'SABIC C/O XYZ LOGISTICS' should resolve to sabic-global with is_role_warning=True."""
        resolver = EntityResolver(con, category="chemical")
        result = resolver.resolve("SABIC C/O XYZ LOGISTICS")

        assert result.get("supplier_id") == "sabic-global", (
            f"Expected sabic-global, got {result.get('supplier_id')}"
        )
        assert result.get("is_role_warning") is True

    def test_clean_sabic_resolves_without_warning(self, con):
        """Direct SABIC name should resolve without a surrogate warning."""
        resolver = EntityResolver(con, category="chemical")
        result = resolver.resolve("SABIC Innovative Plastics")

        assert result.get("supplier_id") == "sabic-global"
        assert result.get("is_role_warning") is not True


# ------------------------------------------------------------------ #
# 4. Role Shield: pre-seeded rejection blocks trader resolution        #
# ------------------------------------------------------------------ #

class TestRoleShieldRejection:

    def _seed_role_shield(self, con, alias: str, canonical_id: str):
        norm = ChemicalNormalizer()
        normalized = norm.normalize(alias)
        con.execute("""
            INSERT INTO entity_rejections (alias_normalized, canonical_id, reason_code)
            VALUES (?, ?, 'role_pollution_trader')
            ON CONFLICT DO NOTHING
        """, [normalized, canonical_id])

    def test_pre_seeded_trader_returns_no_match(self, con):
        """
        After seeding 'XYZ LOGISTICS' as a rejection for sabic-global,
        resolving 'XYZ LOGISTICS' directly should return supplier_id=None.
        """
        self._seed_role_shield(con, "XYZ LOGISTICS", "sabic-global")
        resolver = EntityResolver(con, category="chemical")
        result = resolver.resolve("XYZ LOGISTICS")

        assert result.get("supplier_id") is None, (
            "Role Shield failed: trader resolved to manufacturer canonical"
        )

    def test_role_shield_does_not_block_manufacturer(self, con):
        """Seeding a trader rejection must not block the actual manufacturer."""
        self._seed_role_shield(con, "XYZ LOGISTICS", "sabic-global")
        resolver = EntityResolver(con, category="chemical")
        result = resolver.resolve("SABIC Innovative Plastics")

        assert result.get("supplier_id") == "sabic-global"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
