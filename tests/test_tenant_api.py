"""
Tests for multi-tenant API management and authentication.

Covers:
  - Tenant creation and key provisioning via admin endpoints
  - DB-based API key auth on protected endpoints
  - Suspended tenant / inactive key rejection
  - Monthly quota enforcement (HTTP 429 at tier_1 limit)

Strategy: same in-memory isolation pattern as test_admin_api.py —
env vars set before import, each test gets its own TestClient lifespan.
"""

import os
import sys
import uuid
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Must be set before app import so auth.py startup guard passes
os.environ["ADMIN_TOKEN"] = "dev-admin-pass-123"
os.environ["DB_PATH"] = ":memory:"

from fastapi.testclient import TestClient  # noqa: E402
import api.main  # noqa: E402
from api.auth import hash_key  # noqa: E402
from api.main import app  # noqa: E402

ADMIN = {"X-Admin-Token": "dev-admin-pass-123"}


# ------------------------------------------------------------------ #
# Helpers                                                               #
# ------------------------------------------------------------------ #

def _provision_tenant(client, name: str = "Test Co", tier: str = "tier_1") -> tuple[str, str]:
    """Create a tenant and return (tenant_id, raw_api_key)."""
    r = client.post("/v1/admin/tenants", headers=ADMIN, json={"name": name, "tier": tier})
    assert r.status_code == 200, r.text
    tenant_id = r.json()["tenant_id"]

    r2 = client.post(f"/v1/admin/tenants/{tenant_id}/keys", headers=ADMIN)
    assert r2.status_code == 200, r2.text
    raw_key = r2.json()["api_key"]
    assert raw_key.startswith("dtv_"), "Keys must carry dtv_ prefix"

    return tenant_id, raw_key


def _seed_usage(con, tenant_id: str, count: int) -> None:
    """Seed `count` usage_log rows for the current calendar month."""
    now = datetime.now()
    rows = [(uuid.uuid4().hex, tenant_id, "/v1/score", "POST", 200, now)
            for _ in range(count)]
    con.executemany("""
        INSERT INTO usage_logs (id, tenant_id, endpoint, method, status_code, called_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, rows)


# ------------------------------------------------------------------ #
# Tenant management                                                     #
# ------------------------------------------------------------------ #

def test_create_tenant_returns_id_and_name():
    """POST /admin/tenants creates a tenant and echoes name + tier."""
    with TestClient(app) as client:
        r = client.post(
            "/v1/admin/tenants",
            headers=ADMIN,
            json={"name": "Acme Textiles", "tier": "tier_2"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "Acme Textiles"
        assert body["tier"] == "tier_2"
        assert len(body["tenant_id"]) == 32  # uuid4 hex


def test_create_tenant_rejects_invalid_tier():
    """Only tier_1 / tier_2 / enterprise are valid tier values."""
    with TestClient(app) as client:
        r = client.post(
            "/v1/admin/tenants",
            headers=ADMIN,
            json={"name": "Bad Tier Co", "tier": "free"},
        )
        assert r.status_code == 422


def test_create_key_carries_dtv_prefix():
    """Generated API keys must start with dtv_ for easy identification."""
    with TestClient(app) as client:
        _, raw_key = _provision_tenant(client)
        assert raw_key.startswith("dtv_")
        # Total length: 4 (dtv_) + 48 (24 hex bytes) = 52 chars
        assert len(raw_key) == 52


def test_list_tenants_includes_created_tenant():
    """GET /admin/tenants returns all tenants."""
    with TestClient(app) as client:
        _provision_tenant(client, name="Listed Corp")
        r = client.get("/v1/admin/tenants", headers=ADMIN)
        assert r.status_code == 200
        names = [t["name"] for t in r.json()]
        assert "Listed Corp" in names


def test_create_key_for_missing_tenant_returns_404():
    """Provisioning a key for a nonexistent tenant_id → 404."""
    with TestClient(app) as client:
        r = client.post("/v1/admin/tenants/doesnotexist/keys", headers=ADMIN)
        assert r.status_code == 404


# ------------------------------------------------------------------ #
# Authentication                                                        #
# ------------------------------------------------------------------ #

def test_valid_db_key_reaches_protected_endpoint():
    """
    A freshly provisioned API key must pass DB-based auth on a
    protected endpoint and return 200 (not 403/422/500).

    Uses /v1/resolver/feedback with is_confirmed=False so no alias
    rows need to exist — it just writes to entity_rejections.
    """
    with TestClient(app) as client:
        _, raw_key = _provision_tenant(client)
        r = client.post(
            "/v1/resolver/feedback",
            headers={"X-API-Key": raw_key},
            json={
                "supplier_name": "Test Supplier",
                "canonical_id": "test-canonical",
                "is_confirmed": False,
                "reason_code": "wrong_entity",
            },
        )
        assert r.status_code == 200
        assert r.json()["status"] == "success"


def test_missing_api_key_returns_403():
    """Requests without X-API-Key must be rejected."""
    with TestClient(app) as client:
        r = client.post(
            "/v1/resolver/feedback",
            json={
                "supplier_name": "Test",
                "canonical_id": "test",
                "is_confirmed": False,
            },
        )
        assert r.status_code == 403


def test_invalid_api_key_returns_403():
    """An unrecognised API key must return 403, not 500."""
    with TestClient(app) as client:
        r = client.post(
            "/v1/resolver/feedback",
            headers={"X-API-Key": "dtv_notavalidkey00000000000000000000000000000000000"},
            json={
                "supplier_name": "Test",
                "canonical_id": "test",
                "is_confirmed": False,
            },
        )
        assert r.status_code == 403


def test_suspended_tenant_returns_403():
    """Suspending a tenant must block all their API key requests."""
    with TestClient(app) as client:
        tenant_id, raw_key = _provision_tenant(client, name="Soon Suspended")

        # Suspend the tenant directly in the test DB
        api.main.con.execute(
            "UPDATE tenants SET status = 'suspended' WHERE id = ?", [tenant_id]
        )

        r = client.post(
            "/v1/resolver/feedback",
            headers={"X-API-Key": raw_key},
            json={"supplier_name": "T", "canonical_id": "t", "is_confirmed": False},
        )
        assert r.status_code == 403
        assert "suspended" in r.json()["detail"].lower()


def test_deactivated_key_returns_403():
    """Deactivating a specific key must block it even if the tenant is active."""
    with TestClient(app) as client:
        tenant_id, raw_key = _provision_tenant(client, name="Active Tenant")

        # Deactivate the key directly
        hashed = hash_key(raw_key)
        api.main.con.execute(
            "UPDATE api_keys SET is_active = FALSE WHERE hashed_key = ?", [hashed]
        )

        r = client.post(
            "/v1/resolver/feedback",
            headers={"X-API-Key": raw_key},
            json={"supplier_name": "T", "canonical_id": "t", "is_confirmed": False},
        )
        assert r.status_code == 403


# ------------------------------------------------------------------ #
# Quota enforcement                                                     #
# ------------------------------------------------------------------ #

def test_tier1_quota_blocks_at_1000_calls():
    """
    tier_1 limit is 1,000 calls/month. Seeding 1000 usage_log rows
    for the current month must cause the next request to return 429.
    """
    with TestClient(app) as client:
        tenant_id, raw_key = _provision_tenant(client, name="Heavy Hitter", tier="tier_1")
        _seed_usage(api.main.con, tenant_id, 1000)

        r = client.post(
            "/v1/resolver/feedback",
            headers={"X-API-Key": raw_key},
            json={"supplier_name": "T", "canonical_id": "t", "is_confirmed": False},
        )
        assert r.status_code == 429
        assert "quota" in r.json()["detail"].lower()


def test_tier2_quota_allows_up_to_10000_calls():
    """
    tier_2 limit is 10,000. At 9,999 usage rows the next call must
    still succeed (200), confirming the check is ≥ not >.
    """
    with TestClient(app) as client:
        tenant_id, raw_key = _provision_tenant(client, name="Power User", tier="tier_2")
        _seed_usage(api.main.con, tenant_id, 9_999)

        r = client.post(
            "/v1/resolver/feedback",
            headers={"X-API-Key": raw_key},
            json={"supplier_name": "T", "canonical_id": "t", "is_confirmed": False},
        )
        assert r.status_code == 200


def test_enterprise_tier_has_no_quota():
    """enterprise tier is unlimited — even 20,000 seeded rows must not block."""
    with TestClient(app) as client:
        tenant_id, raw_key = _provision_tenant(client, name="Big Client", tier="enterprise")
        _seed_usage(api.main.con, tenant_id, 20_000)

        r = client.post(
            "/v1/resolver/feedback",
            headers={"X-API-Key": raw_key},
            json={"supplier_name": "T", "canonical_id": "t", "is_confirmed": False},
        )
        assert r.status_code == 200


# ------------------------------------------------------------------ #
# Usage analytics                                                       #
# ------------------------------------------------------------------ #

def test_usage_analytics_aggregates_by_tenant_and_endpoint():
    """GET /admin/usage returns endpoint-level call counts per tenant."""
    with TestClient(app) as client:
        tenant_id, _ = _provision_tenant(client, name="Analytics Co")
        _seed_usage(api.main.con, tenant_id, 5)

        r = client.get("/v1/admin/usage", headers=ADMIN)
        assert r.status_code == 200
        rows = r.json()
        # Find the row for our tenant
        our_rows = [row for row in rows if row["tenant_name"] == "Analytics Co"]
        assert len(our_rows) == 1
        assert our_rows[0]["calls"] == 5
        assert our_rows[0]["endpoint"] == "/v1/score"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
