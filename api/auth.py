import os
from fastapi import Security, HTTPException, status
from fastapi.security.api_key import APIKeyHeader
from dotenv import load_dotenv

load_dotenv()

# We look for X-API-Key in the request headers
API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

# Admin security header for the Review Dashboard
ADMIN_TOKEN_NAME = "X-Admin-Token"
admin_token_header = APIKeyHeader(name=ADMIN_TOKEN_NAME, auto_error=False)

EXPECTED_API_KEY = os.getenv("API_KEY", "dev-trust-key-99")
EXPECTED_ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "dev-admin-pass-123")

async def get_api_key(api_key_header: str = Security(api_key_header)):
    """Dependency to validate the general API key."""
    if api_key_header == EXPECTED_API_KEY:
        return api_key_header
    raise HTTPException(status_code=403, detail="Invalid X-API-Key")

async def get_admin_key(admin_token_header: str = Security(admin_token_header)):
    """Dependency to validate the Admin token."""
    if admin_token_header == EXPECTED_ADMIN_TOKEN:
        return admin_token_header
    raise HTTPException(status_code=403, detail="Invalid X-Admin-Token")
