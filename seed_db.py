import duckdb
import os
import uuid
import hashlib
from dotenv import load_dotenv

load_dotenv()

DB_PATH = "data/trust_engine.duckdb"

def seed_db():
    print(f"Connecting to {DB_PATH}...")
    con = duckdb.connect(DB_PATH)
    
    # Ensure tables exist (redundant but safe)
    # Using the same schema from db.py
    con.execute("""
        CREATE TABLE IF NOT EXISTS tenants (
            id                 VARCHAR PRIMARY KEY,
            name               VARCHAR NOT NULL,
            tier               VARCHAR DEFAULT 'tier_1',
            status             VARCHAR DEFAULT 'active',
            created_at         TIMESTAMP DEFAULT NOW()
        );
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            hashed_key         VARCHAR PRIMARY KEY,
            tenant_id          VARCHAR REFERENCES tenants(id),
            prefix             VARCHAR NOT NULL,
            is_active          BOOLEAN DEFAULT TRUE,
            created_at         TIMESTAMP DEFAULT NOW(),
            last_used_at       TIMESTAMP
        );
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS usage_logs (
            id                 VARCHAR PRIMARY KEY,
            tenant_id          VARCHAR REFERENCES tenants(id),
            endpoint           VARCHAR NOT NULL,
            method             VARCHAR NOT NULL,
            status_code        INTEGER,
            called_at          TIMESTAMP DEFAULT NOW()
        );
    """)

    # Seed Admin Tenant
    admin_id = uuid.uuid4().hex
    # check if Admin exists
    exists = con.execute("SELECT id FROM tenants WHERE name = 'Global Admin'").fetchone()
    if exists:
        print("Admin tenant already exists.")
    else:
        con.execute("""
            INSERT INTO tenants (id, name, tier, status)
            VALUES (?, 'Global Admin', 'enterprise', 'active')
        """, [admin_id])
        print(f"Created Global Admin tenant (ID: {admin_id})")

        # Get API_KEY from .env
        raw_key = os.getenv("API_KEY")
        if raw_key:
            hashed_key = hashlib.sha256(raw_key.encode()).hexdigest()
            prefix = raw_key[:8]
            con.execute("""
                INSERT INTO api_keys (hashed_key, tenant_id, prefix, is_active)
                VALUES (?, ?, ?, TRUE)
            """, [hashed_key, admin_id, prefix])
            print(f"Assigned API_KEY (prefix: {prefix}) to Admin tenant.")
        else:
            print("API_KEY not found in .env. Skipping key association.")

    con.close()
    print("Database seeding complete.")

if __name__ == "__main__":
    seed_db()
