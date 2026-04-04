"""
Smoke tests — run these BEFORE the scraper to verify the full stack works.

Usage:
    python tests/test_smoke.py

All tests use the seeded data (no network calls, no ImportYeti login needed).
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tempfile
import unittest
import pandas as pd

from pipeline.storage.db import init_db, upsert_supplier, upsert_certification
from model.features import engineer_features, MODEL_FEATURES


SAMPLE_SUPPLIER = {
    "id": "test-supplier-001",
    "name": "Test Textile Co",
    "country": "India",
    "address": "Mumbai, India",
    "shipment_count": 120,
    "avg_monthly_shipments": 10.0,
    "total_buyers": 8,
    "hs_codes": ["5208", "6109"],
    "top_buyers": ["Buyer A", "Buyer B"],
    "first_shipment_date": "2018-01-01",
    "last_shipment_date": "2025-01-01",
    "source": "test",
    "raw_url": None,
}

SAMPLE_CERT = {
    "supplier_id": "test-supplier-001",
    "source": "oekotex",
    "license_id": "TEST-001",
    "status": "valid",
    "valid_until": "2026-01-01",
    "certificate_name": "Test Cert",
}


class TestDatabase(unittest.TestCase):

    def setUp(self):
        # Use a temp DB for each test
        self.tmp = tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False)
        self.con = init_db(path=self.tmp.name)

    def tearDown(self):
        self.con.close()
        os.unlink(self.tmp.name)

    def test_upsert_supplier(self):
        upsert_supplier(self.con, SAMPLE_SUPPLIER)
        row = self.con.execute(
            "SELECT id, name, shipment_count FROM suppliers WHERE id = ?",
            ["test-supplier-001"]
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[1], "Test Textile Co")
        self.assertEqual(row[2], 120)

    def test_upsert_supplier_idempotent(self):
        upsert_supplier(self.con, SAMPLE_SUPPLIER)
        upsert_supplier(self.con, SAMPLE_SUPPLIER)  # Should not raise
        count = self.con.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]
        self.assertEqual(count, 1)

    def test_upsert_certification(self):
        upsert_supplier(self.con, SAMPLE_SUPPLIER)
        upsert_certification(self.con, SAMPLE_CERT)
        row = self.con.execute(
            "SELECT status FROM certifications WHERE supplier_id = ?",
            ["test-supplier-001"]
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "valid")

    def test_all_tables_exist(self):
        tables = self.con.execute("SHOW TABLES").fetchall()
        table_names = {t[0] for t in tables}
        self.assertIn("suppliers", table_names)
        self.assertIn("certifications", table_names)
        self.assertIn("shipments", table_names)
        self.assertIn("trust_scores", table_names)


class TestFeatureEngineering(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False)
        self.con = init_db(path=self.tmp.name)
        upsert_supplier(self.con, SAMPLE_SUPPLIER)
        upsert_certification(self.con, SAMPLE_CERT)

    def tearDown(self):
        self.con.close()
        os.unlink(self.tmp.name)

    def test_feature_engineering_runs(self):
        df = engineer_features(self.con)
        self.assertFalse(df.empty)
        self.assertEqual(len(df), 1)

    def test_all_model_features_present(self):
        df = engineer_features(self.con)
        for feat in MODEL_FEATURES:
            self.assertIn(feat, df.columns, f"Missing feature: {feat}")

    def test_years_active_positive(self):
        df = engineer_features(self.con)
        self.assertGreater(df["years_active"].iloc[0], 0)

    def test_certification_score(self):
        df = engineer_features(self.con)
        # Should have 1 OEKO-TEX valid cert → score = 1
        self.assertGreaterEqual(df["certification_score"].iloc[0], 1)

    def test_customer_concentration_ratio(self):
        df = engineer_features(self.con)
        # 8 buyers → ratio = 1/8 = 0.125
        ratio = df["customer_concentration_ratio"].iloc[0]
        self.assertAlmostEqual(ratio, 1/8, places=3)

    def test_no_nulls_in_model_features(self):
        df = engineer_features(self.con)
        for feat in MODEL_FEATURES:
            nulls = df[feat].isna().sum()
            self.assertEqual(nulls, 0, f"Feature {feat} has {nulls} nulls")


if __name__ == "__main__":
    print("Running smoke tests...\n")
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestDatabase))
    suite.addTests(loader.loadTestsFromTestCase(TestFeatureEngineering))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    if result.wasSuccessful():
        print("\n✅ All smoke tests passed. Stack is working.")
        sys.exit(0)
    else:
        print("\n❌ Some tests failed. Fix before running the scraper.")
        sys.exit(1)
