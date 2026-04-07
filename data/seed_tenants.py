import os
import uuid
from api.auth import hash_key
from pipeline.storage.db import init_db
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

def seed_tenants():
    con = init_db()
    
    # Check if we already have tenants
    count = con.execute("SELECT COUNT(*) FROM tenants").fetchone()[0]
    if count > 0:
        logger.info("Tenants already exist, skipping seed.")
        con.close()
        return

    logger.info("Seeding initial Admin tenant...")
    
    admin_id = uuid.uuid4().hex
    con.execute("""
        INSERT INTO tenants (id, name, tier, status)
        VALUES (?, 'Global Admin', 'enterprise', 'active')
    """, [admin_id])

    # Get API_KEY from environment to preserve access
    raw_key = os.getenv("API_KEY")
    if not raw_key:
        logger.warning("API_KEY not found in .env, generating a random one...")
        import secrets
        raw_key = secrets.token_hex(32)
        print(f"\nNEW API_KEY GENERATED: {raw_key}\nPLEASE UPDATE YOUR .env FILE!\n")

    hashed_key = hash_key(raw_key)
    prefix = raw_key[:8]

    con.execute("""
        INSERT INTO api_keys (hashed_key, tenant_id, prefix, is_active)
        VALUES (?, ?, ?, TRUE)
    """, [hashed_key, admin_id, prefix])

    logger.success(f"Admin tenant created (ID: {admin_id}) with API key prefix: {prefix}")
    con.close()

if __name__ == "__main__":
    seed_tenants()
