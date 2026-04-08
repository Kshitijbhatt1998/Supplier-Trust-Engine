import os
import hashlib
from datetime import datetime, timedelta
from typing import Optional
from pydantic import BaseModel, EmailStr, Field
from fastapi import Security, HTTPException, Request, status
from fastapi.security.api_key import APIKeyHeader
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from dotenv import load_dotenv

load_dotenv()

# ── Configuration ────────────────────────────────────────────────── #
API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

ADMIN_TOKEN_NAME = "X-Admin-Token"
admin_token_header = APIKeyHeader(name=ADMIN_TOKEN_NAME, auto_error=False)

# JWT Settings
SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not SECRET_KEY:
    # Fallback to ADMIN_TOKEN if JWT_SECRET_KEY is missing (not ideal for prod)
    SECRET_KEY = os.getenv("ADMIN_TOKEN", "insecure_default_secret")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 # 24 hours

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="v1/auth/login", auto_error=False)

_admin_token = os.getenv("ADMIN_TOKEN")
if not _admin_token:
    raise ValueError("ADMIN_TOKEN environment variable is not set.")

EXPECTED_ADMIN_TOKEN: str = _admin_token

# Global KDF salt for API key hashing. Override via environment in production.
API_KEY_KDF_SALT: bytes = os.getenv("API_KEY_KDF_SALT", "CHANGE_ME_API_KEY_KDF_SALT").encode()

# Monthly call quotas per tier. None = unlimited.
TIER_QUOTA: dict[str, int | None] = {
    "tier_1":     1_000,
    "tier_2":     10_000,
    "enterprise": None,
}

# Real-time Rate limits (Requests Per Minute)
TIER_RPM: dict[str, str] = {
    "tier_1":     "20/minute",
    "tier_2":     "100/minute",
    "enterprise": "1000/minute",
}

def get_tenant_limit_key(request: Request) -> str:
    """
    Key function for slowapi. 
    Attempts to identify the tenant/user to apply specific rate limits.
    """
    # 1. Try to get tenant from request state (if set by dependency)
    tenant = getattr(request.state, "tenant", None)
    if tenant:
        return f"tenant:{tenant.id}"
    
    # 2. Try to get user from request state
    user = getattr(request.state, "user", None)
    if user:
        return f"user:{user.id}"
        
    # 3. Fallback to IP
    return request.client.host if request.client else "127.0.0.1"

# ── Models ───────────────────────────────────────────────────────── #

class Tenant(BaseModel):
    id: str
    name: str
    tier: str
    status: str

class User(BaseModel):
    id: str
    email: str
    full_name: Optional[str] = None
    role: str
    tenant_id: Optional[str] = None

class UserInDB(User):
    hashed_password: str

# ── Hashing & Tokens ────────────────────────────────────────────── #

def hash_key(key: str) -> str:
    """Derive a hardened hash of an API key for lookup without storing raw key material."""
    # Use a computationally expensive KDF (PBKDF2-HMAC-SHA256) instead of a single fast hash.
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        key.encode(),
        API_KEY_KDF_SALT,
        100_000,
    )
    return dk.hex()

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters long")
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# ── Dependencies ─────────────────────────────────────────────────── #

async def get_current_user(
    request: Request,
    token: str = Security(oauth2_scheme)
) -> User:
    """Validate JWT and return the user from the database."""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Could not validate credentials")

    con = request.app.state.db
    row = con.execute("""
        SELECT id, email, full_name, role, tenant_id FROM users WHERE email = ?
    """, [email]).fetchone()
    
    if row is None:
        raise HTTPException(status_code=401, detail="User not found")
    
    # Store user in request state for rate limiter
    request.state.user = User(id=row[0], email=row[1], full_name=row[2], role=row[3], tenant_id=row[4])
    return request.state.user


async def get_current_tenant(
    request: Request,
    api_key_header: str = Security(api_key_header),
) -> Tenant:
    """Validate API key and return associated Tenant."""
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

    # Quota check
    quota = TIER_QUOTA.get(tier)
    if quota is not None:
        month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        used = con.execute("""
            SELECT COUNT(*) FROM usage_logs
            WHERE tenant_id = ? AND called_at >= ?
        """, [tenant_id, month_start]).fetchone()[0]
        if used >= quota:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Monthly quota of {quota:,} requests exceeded.",
            )

    # Store tenant in request state for rate limiter
    request.state.tenant = Tenant(id=tenant_id, name=name, tier=tier, status=t_status)
    return request.state.tenant


async def get_admin_key(
    admin_token_header: str = Security(admin_token_header),
    current_user: Optional[User] = Security(get_current_user)
):
    """
    Dependency to validate either:
    1. The legacy static X-Admin-Token header.
    2. A valid JWT from a user with role='admin'.
    """
    # 1. Try legacy token
    if admin_token_header and admin_token_header == EXPECTED_ADMIN_TOKEN:
        return admin_token_header
    
    # 2. Try JWT User role
    if current_user and current_user.role == 'admin':
        return current_user.email

    raise HTTPException(status_code=403, detail="Administrative access required")
