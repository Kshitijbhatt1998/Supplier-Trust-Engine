import os
from fastapi import Security, HTTPException, status
from fastapi.security.api_key import APIKeyHeader
from dotenv import load_dotenv

load_dotenv()

# We look for X-API-Key in the request headers
API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

# This is our expected secret key, ideally stored in .env
# For local dev, we default to 'dev-trust-key-99'
EXPECTED_API_KEY = os.getenv("API_KEY", "dev-trust-key-99")

async def get_api_key(api_key_header: str = Security(api_key_header)):
    """
    Dependency to validate the API key.
    If the key is missing or incorrect, it raises a 403 Forbidden.
    """
    if api_key_header == EXPECTED_API_KEY:
        return api_key_header
    
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Could not validate API Key. Please provide X-API-Key header."
    )
