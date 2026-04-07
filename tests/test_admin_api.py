"""
Tests for the Admin Review Queue API.

Strategy:
  - Set DB_PATH=:memory: before importing the app so the lifespan
    creates a fresh in-memory DuckDB on every TestClient context.
  - Each test opens its own `with TestClient(app)` block, which runs
    the full lifespan (init_db → yield → close), giving complete
    isolation between tests.
  - After `TestClient.__enter__` the lifespan has already set
    `api.main.con`; we seed data directly through that reference.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Must be set before importing the app so the lifespan uses :memory:
os.environ["DB_PATH"] = ":memory:"
os.environ["ADMIN_TOKEN"] = "dev-admin-pass-123"

from fastapi.testclient import TestClient  # noqa: E402
import api.main  # noqa: E402
from api.main import app  # noqa: E402

ADMIN = {"X-Admin-Token": "dev-admin-pass-123"}


# ------------------------------------------------------------------ #
# Helpers                                                               #
# ------------------------------------------------------------------ #

def _seed(con):
    """
    Insert two suppliers and two unverified aliases.

    Expected priority scores (P = 0.4*cap(V,100)/100 + 0.3*T/100 + 0.3*S/100):

      a1 → V=150 (capped→1.0), T=40, S=70
           P = 0.40 + 0.12 + 0.21 = 0.730

      a2 → V=10,  T=95, S=95
           P = 0.04 + 0.285 + 0.285 = 0.610

    a1 should always rank above a2 despite its supplier having a lower trust score,
    because capped volume dominates at maximum weight.
    """
    con.execute("INSERT INTO suppliers (id, name, country) VALUES ('s-low',  'Low Trust Corp',  'India')")
    con.execute("INSERT INTO suppliers (id, name, country) VALUES ('s-high', 'High Trust Corp', 'India')")
    con.execute("INSERT INTO trust_scores (supplier_id, trust_score) VALUES ('s-low',  40)")
    con.execute("INSERT INTO trust_scores (supplier_id, trust_score) VALUES ('s-high', 95)")
    con.execute("""
        INSERT INTO entity_aliases
            (id, alias_name, alias_normalized, canonical_id, match_score, suggestion_count, is_verified)
        VALUES ('a1', 'LowHit', 'lowhit', 's-low', 70.0, 150, FALSE)
    """)
    con.execute("""
        INSERT INTO entity_aliases
            (id, alias_name, alias_normalized, canonical_id, match_score, suggestion_count, is_verified)
        VALUES ('a2', 'HighHit', 'highhit', 's-high', 95.0, 10, FALSE)
    """)


# ------------------------------------------------------------------ #
# Tests                                                                 #
# ------------------------------------------------------------------ #

def test_priority_ordering():
    """
    Capped-volume alias (a1) must appear first despite its supplier
    having a lower trust score than the second alias (a2).
    """
    with TestClient(app) as client:
        _seed(api.main.con)

        resp = client.get("/v1/admin/review-queue", headers=ADMIN)
        assert resp.status_code == 200

        data = resp.json()
        assert len(data) == 2

        ids = [item["id"] for item in data]
        p_scores = {item["id"]: item["priority_score"] for item in data}
        assert ids[0] == "a1", (
            f"a1 (P={p_scores.get('a1'):.4f}) should outrank "
            f"a2 (P={p_scores.get('a2'):.4f})"
        )


def test_verify_removes_from_queue():
    """Verifying an alias removes it from the queue; others remain."""
    with TestClient(app) as client:
        _seed(api.main.con)

        resp = client.post(
            "/v1/admin/alias/action",
            headers=ADMIN,
            json={"alias_ids": ["a1"], "action": "verify"},
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

        queue = client.get("/v1/admin/review-queue", headers=ADMIN).json()
        ids = {item["id"] for item in queue}
        assert "a1" not in ids
        assert "a2" in ids

        # Confirm is_verified was flipped, not deleted
        row = api.main.con.execute(
            "SELECT is_verified FROM entity_aliases WHERE id = 'a1'"
        ).fetchone()
        assert row is not None and row[0] is True


def test_reject_moves_to_rejections_table():
    """
    Rejecting an alias must:
      - delete it from entity_aliases
      - insert a row into entity_rejections with the correct fields
    """
    with TestClient(app) as client:
        _seed(api.main.con)

        resp = client.post(
            "/v1/admin/alias/action",
            headers=ADMIN,
            json={"alias_ids": ["a2"], "action": "reject", "reason_code": "wrong_entity"},
        )
        assert resp.status_code == 200

        con = api.main.con

        still_there = con.execute(
            "SELECT id FROM entity_aliases WHERE id = 'a2'"
        ).fetchone()
        assert still_there is None, "Rejected alias must be deleted from entity_aliases"

        rej = con.execute(
            "SELECT alias_normalized, canonical_id, reason_code "
            "FROM entity_rejections WHERE canonical_id = 's-high'"
        ).fetchone()
        assert rej is not None
        assert rej[0] == "highhit"
        assert rej[1] == "s-high"
        assert rej[2] == "wrong_entity"


def test_bulk_verify_all_for_supplier():
    """Bulk-approving all aliases empties the queue."""
    with TestClient(app) as client:
        _seed(api.main.con)

        resp = client.post(
            "/v1/admin/alias/action",
            headers=ADMIN,
            json={"alias_ids": ["a1", "a2"], "action": "verify"},
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 2

        queue = client.get("/v1/admin/review-queue", headers=ADMIN).json()
        assert len(queue) == 0, "Queue must be empty after bulk verify"


def test_empty_ids_is_ignored():
    """Sending an empty alias_ids list returns a graceful 'ignored' response."""
    with TestClient(app) as client:
        resp = client.post(
            "/v1/admin/alias/action",
            headers=ADMIN,
            json={"alias_ids": [], "action": "verify"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"


def test_category_filter_isolates_chemical_from_textile():
    """
    Seeding one textile alias and one chemical alias then querying with
    ?category=chemical must return only the chemical alias, and vice-versa.
    Also verifies the cas_number field is present only for chemical rows.
    """
    with TestClient(app) as client:
        con = api.main.con
        # Textile supplier + alias
        con.execute("INSERT INTO suppliers (id, name, country, category) VALUES ('s-tex', 'Welspun India Ltd', 'India', 'textile')")
        con.execute("INSERT INTO trust_scores (supplier_id, trust_score) VALUES ('s-tex', 80)")
        con.execute("""
            INSERT INTO entity_aliases
                (id, alias_name, alias_normalized, canonical_id, match_score, suggestion_count, is_verified, category)
            VALUES ('a-tex', 'Welsun India', 'welsun india', 's-tex', 82.0, 5, FALSE, 'textile')
        """)

        # Chemical supplier + alias (CAS-anchored canonical)
        con.execute("INSERT INTO suppliers (id, name, country, category) VALUES ('cas-9002-88-4', 'Polyethylene (HDPE)', 'India', 'chemical')")
        con.execute("INSERT INTO trust_scores (supplier_id, trust_score) VALUES ('cas-9002-88-4', 70)")
        con.execute("""
            INSERT INTO entity_aliases
                (id, alias_name, alias_normalized, canonical_id, match_score, suggestion_count, is_verified, category)
            VALUES ('a-chem', 'HDPE Granules', 'high density polyethylene granules', 'cas-9002-88-4', 88.0, 3, FALSE, 'chemical')
        """)

        # Unfiltered — both visible
        all_rows = client.get("/v1/admin/review-queue", headers=ADMIN).json()
        all_ids  = {r["id"] for r in all_rows}
        assert "a-tex"  in all_ids
        assert "a-chem" in all_ids

        # Chemical filter — only chemical alias returned
        chem_rows = client.get("/v1/admin/review-queue?category=chemical", headers=ADMIN).json()
        assert len(chem_rows) == 1
        assert chem_rows[0]["id"] == "a-chem"
        assert chem_rows[0]["cas_number"] == "9002-88-4"

        # Textile filter — only textile alias returned
        tex_rows = client.get("/v1/admin/review-queue?category=textile", headers=ADMIN).json()
        assert len(tex_rows) == 1
        assert tex_rows[0]["id"] == "a-tex"
        assert tex_rows[0]["cas_number"] is None


def test_unauthorized_request_returns_403():
    """Wrong admin token must be rejected with 403."""
    with TestClient(app) as client:
        resp = client.get(
            "/v1/admin/review-queue",
            headers={"X-Admin-Token": "not-the-right-token"},
        )
        assert resp.status_code == 403


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
