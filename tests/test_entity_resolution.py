"""
Unit tests for the entity resolution layer.

Covers:
  - normalize() correctness
  - Fast path: alias table exact match
  - Fuzzy match above threshold (reordering)
  - Abbreviation match via partial_ratio
  - Below-threshold → new entity created
  - Slug collision guard
  - resolve_and_upsert() idempotency
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.storage.db import init_db, upsert_supplier
from pipeline.entity_resolution import EntityResolver, normalize, resolve_and_upsert


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _tmp_db():
    f = tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False)
    f.close()
    try:
        os.unlink(f.name)
    except OSError:
        pass
    return init_db(path=f.name), f.name


def _seed(con, name, country="India", source="importyeti"):
    sup_id = name.lower().replace(" ", "-")[:40]
    upsert_supplier(con, {
        "id": sup_id,
        "name": name,
        "country": country,
        "address": None,
        "shipment_count": 100,
        "avg_monthly_shipments": 8.0,
        "total_buyers": 6,
        "hs_codes": ["6109"],
        "top_buyers": ["Buyer A"],
        "first_shipment_date": "2018-01-01",
        "last_shipment_date": "2025-01-01",
        "source": source,
        "raw_url": None,
    })
    return sup_id


# ------------------------------------------------------------------ #
# normalize()                                                          #
# ------------------------------------------------------------------ #

class TestNormalize(unittest.TestCase):

    def test_casefold_and_suffix_strip(self):
        self.assertEqual(normalize("WELSPUN INDIA LIMITED"), normalize("Welspun India Ltd"))

    def test_token_sort(self):
        # "India Welspun" and "Welspun India" normalize to the same token-sorted form
        self.assertEqual(normalize("India Welspun"), normalize("Welspun India"))

    def test_abbreviation_suffix_stripped(self):
        # "Pvt. Ltd." stripped, punctuation removed
        self.assertEqual(normalize("Shahi Exports Pvt. Ltd."), normalize("Shahi Exports"))

    def test_all_caps_equals_title(self):
        self.assertEqual(normalize("SHAHI EXPORTS PVT LTD"), normalize("Shahi Exports"))

    def test_punctuation_stripped(self):
        norm = normalize("Orient Craft (Exports) Pvt. Ltd.")
        self.assertNotIn("(", norm)
        self.assertNotIn(")", norm)
        self.assertNotIn(".", norm)

    def test_single_word(self):
        self.assertEqual(normalize("Welspun"), "welspun")

    def test_empty_after_stripping(self):
        # If all tokens stripped, normalize gracefully returns empty string
        result = normalize("Ltd.")
        self.assertIsInstance(result, str)


# ------------------------------------------------------------------ #
# EntityResolver                                                       #
# ------------------------------------------------------------------ #

class TestEntityResolver(unittest.TestCase):

    def setUp(self):
        self.con, self.db_path = _tmp_db()
        self.resolver = EntityResolver(self.con, threshold=85)

    def tearDown(self):
        self.con.close()
        os.unlink(self.db_path)

    # -- fast path (alias) ------------------------------------------

    def test_alias_fast_path(self):
        """Second resolve of same normalized form hits alias table, not fuzzy scan."""
        canonical_id = _seed(self.con, "Welspun India Ltd")

        # First resolve creates alias
        r1 = self.resolver.resolve("Welspun India Ltd", country="India", source="importyeti")
        self.assertFalse(r1.is_new)
        self.assertEqual(r1.score, 100.0)

        # Second resolve with different casing hits fast path
        r2 = self.resolver.resolve("WELSPUN INDIA LIMITED", country="India", source="bol")
        self.assertFalse(r2.is_new)
        self.assertEqual(r2.canonical_id, canonical_id)
        self.assertEqual(r2.score, 100.0)

    # -- fuzzy match ------------------------------------------------

    def test_reordering_match(self):
        """'India Welspun' should match 'Welspun India Ltd' via token_sort_ratio."""
        canonical_id = _seed(self.con, "Welspun India Ltd")
        r = self.resolver.resolve("India Welspun", country="India", source="bol")
        self.assertFalse(r.is_new)
        self.assertEqual(r.canonical_id, canonical_id)
        self.assertGreaterEqual(r.score, 85)

    def test_all_caps_match(self):
        """'WELSPUN INDIA LIMITED' should match 'Welspun India Ltd'."""
        canonical_id = _seed(self.con, "Welspun India Ltd")
        r = self.resolver.resolve("WELSPUN INDIA LIMITED", country="India", source="bol")
        self.assertFalse(r.is_new)
        self.assertEqual(r.canonical_id, canonical_id)

    def test_abbreviation_match(self):
        """Short form 'Welspun' should fuzzy-match 'Welspun India Ltd' via partial_ratio."""
        canonical_id = _seed(self.con, "Welspun India Ltd")
        r = self.resolver.resolve("Welspun", country="India", source="indiamart")
        # partial_ratio contribution should push score above threshold
        self.assertFalse(r.is_new)
        self.assertEqual(r.canonical_id, canonical_id)

    def test_country_blocking_prevents_cross_country_merge(self):
        """Two suppliers with same name in different countries must NOT merge."""
        india_id = _seed(self.con, "Orient Craft", country="India")
        turkey_id = _seed(self.con, "Orient Craft", country="Turkey")

        r_india = self.resolver.resolve("Orient Craft", country="India", source="importyeti")
        r_turkey = self.resolver.resolve("Orient Craft", country="Turkey", source="importyeti")

        self.assertEqual(r_india.canonical_id, india_id)
        self.assertEqual(r_turkey.canonical_id, turkey_id)

    # -- below threshold → new entity --------------------------------

    def test_unrelated_name_creates_new(self):
        """Completely different name should create a new entity."""
        _seed(self.con, "Welspun India Ltd")
        r = self.resolver.resolve("Shahi Exports", country="India", source="importyeti")
        self.assertTrue(r.is_new)
        self.assertEqual(r.score, 0.0)

    def test_new_entity_canonical_id_slugified(self):
        r = self.resolver.resolve("Shahi Exports Pvt Ltd", country="India", source="importyeti")
        self.assertTrue(r.is_new)
        self.assertIn("shahi", r.canonical_id)

    # -- alias persistence ------------------------------------------

    def test_alias_written_to_db(self):
        _seed(self.con, "Welspun India Ltd")
        self.resolver.resolve("WELSPUN INDIA LIMITED", country="India", source="bol")
        row = self.con.execute(
            "SELECT canonical_id FROM entity_aliases WHERE alias_normalized = ?",
            [normalize("WELSPUN INDIA LIMITED")],
        ).fetchone()
        self.assertIsNotNone(row)

    def test_new_entity_alias_registered(self):
        """New entities also get their canonical alias registered."""
        r = self.resolver.resolve("Brand New Supplier Co", source="importyeti")
        row = self.con.execute(
            "SELECT canonical_id FROM entity_aliases WHERE canonical_id = ?",
            [r.canonical_id],
        ).fetchone()
        self.assertIsNotNone(row)


# ------------------------------------------------------------------ #
# resolve_and_upsert()                                                 #
# ------------------------------------------------------------------ #

class TestResolveAndUpsert(unittest.TestCase):

    def setUp(self):
        self.con, self.db_path = _tmp_db()

    def tearDown(self):
        self.con.close()
        os.unlink(self.db_path)

    def _supplier_dict(self, name, country="India", source="importyeti", shipments=100):
        return {
            "name": name,
            "country": country,
            "address": None,
            "shipment_count": shipments,
            "avg_monthly_shipments": 8.0,
            "total_buyers": 6,
            "hs_codes": ["6109"],
            "top_buyers": ["Buyer A"],
            "first_shipment_date": "2018-01-01",
            "last_shipment_date": "2025-01-01",
            "source": source,
            "raw_url": None,
        }

    def test_first_call_creates_supplier(self):
        r = resolve_and_upsert(self.con, self._supplier_dict("Welspun India Ltd"))
        self.assertTrue(r.is_new)
        row = self.con.execute(
            "SELECT id FROM suppliers WHERE id = ?", [r.canonical_id]
        ).fetchone()
        self.assertIsNotNone(row)

    def test_second_call_same_entity_no_duplicate(self):
        resolve_and_upsert(self.con, self._supplier_dict("Welspun India Ltd"))
        resolve_and_upsert(self.con, self._supplier_dict("WELSPUN INDIA LIMITED", source="bol"))
        count = self.con.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]
        self.assertEqual(count, 1)

    def test_abbreviated_name_merges(self):
        resolve_and_upsert(self.con, self._supplier_dict("Welspun India Ltd"))
        r = resolve_and_upsert(self.con, self._supplier_dict("Welspun", source="indiamart"))
        self.assertFalse(r.is_new)
        count = self.con.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]
        self.assertEqual(count, 1)

    def test_different_supplier_creates_second_record(self):
        resolve_and_upsert(self.con, self._supplier_dict("Welspun India Ltd"))
        resolve_and_upsert(self.con, self._supplier_dict("Shahi Exports"))
        count = self.con.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]
        self.assertEqual(count, 2)

    def test_idempotent_triple_call(self):
        for _ in range(3):
            resolve_and_upsert(self.con, self._supplier_dict("Welspun India Ltd"))
        count = self.con.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]
        self.assertEqual(count, 1)


# ------------------------------------------------------------------ #

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestNormalize))
    suite.addTests(loader.loadTestsFromTestCase(TestEntityResolver))
    suite.addTests(loader.loadTestsFromTestCase(TestResolveAndUpsert))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
