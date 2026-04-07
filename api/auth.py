import os
import hashlib
from datetime import datetime
from pydantic import BaseModel
from fastapi import Security, HTTPException, Request, status
from fastapi.security.api_key import APIKeyHeader
from dotenv import load_dotenv

load_dotenv()

# Headers
API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

ADMIN_TOKEN_NAME = "X-Admin-Token"
admin_token_header = APIKeyHeader(name=ADMIN_TOKEN_NAME, auto_error=False)

_admin_token = os.getenv("ADMIN_TOKEN")
if not _admin_token:
    raise ValueError("ADMIN_TOKEN environment variable is not set.")

EXPECTED_ADMIN_TOKEN: str = _admin_token

# Monthly call quotas per tier. None = unlimited.
TIER_QUOTA: dict[str, int | None] = {
    "tier_1":     1_000,
    "tier_2":     10_000,
    "enterprise": None,
}


class Tenant(BaseModel):
    id: str
    name: str
    tier: str
    status: str


def hash_key(key: str) -> str:
    """Hash an API key for secure lookup without storing raw key material."""
    return hashlib.sha3_256(key.encode()).hexdigest()


async def get_current_tenant(
    request: Request,
    api_key_header: str = Security(api_key_header),
) -> Tenant:
    """
    Validate the API key against the database, enforce monthly quota,
    and return the Tenant object associated with the key.
    """
    if not api_key_header:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="X-API-Key header is missing",
        )

    con = request.app.state.db
    hashed = hash_key(api_key_header)

    row = con.execute("""
        SELECT t.id, t.name, t.tier, t.status, k.is_active
        FROM tenants t
        JOIN api_keys k ON k.tenant_id = t.id
        WHERE k.hashed_key = ?
    """, [hashed]).fetchone()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API Key",
        )

    tenant_id, name, tier, t_status, k_active = row

    if not k_active or t_status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API Key or Tenant is suspended",
        )

    # ── Monthly quota check ──────────────────────────────────────────
    quota = TIER_QUOTA.get(tier)
    if quota is not None:
        month_start = datetime.now().replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        used = con.execute("""
            SELECT COUNT(*) FROM usage_logs
            WHERE tenant_id = ? AND called_at >= ?
        """, [tenant_id, month_start]).fetchone()[0]
        if used >= quota:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Monthly quota of {quota:,} requests exceeded. "
                    "Upgrade your plan at datavibe.io/billing."
                ),
            )

    return Tenant(id=tenant_id, name=name, tier=tier, status=t_status)

# For backward compatibility and easier adoption in current endpoints
async def get_api_key(tenant: Tenant = Security(get_current_tenant)) -> str:
    """Legacy wrapper that returns the tenant ID as the 'key'."""
    return tenant.id

async def get_admin_key(admin_token_header: str = Security(admin_token_header)):
    """Dependency to validate the Admin token (global)."""
    if admin_token_header == EXPECTED_ADMIN_TOKEN:
        return admin_token_header
    raise HTTPException(status_code=403, detail="Invalid X-Admin-Token")
