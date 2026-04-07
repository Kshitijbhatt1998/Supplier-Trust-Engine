import sys
import os
import uuid

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from api.auth import get_password_hash
from pipeline.storage.db import init_db

def seed_admin_user():
    con = init_db()
    
    # 1. Ensure a tenant exists (Global Admin)
    # Check if a tenant already exists
    tenant = con.execute("SELECT id FROM tenants LIMIT 1").fetchone()
    if not tenant:
        tenant_id = uuid.uuid4().hex
        con.execute("""
            INSERT INTO tenants (id, name, tier, status)
            VALUES (?, 'Global Admin', 'enterprise', 'active')
        """, [tenant_id])
        print(f"Created Global Admin tenant: {tenant_id}")
    else:
        tenant_id = tenant[0]
        print(f"Using existing tenant: {tenant_id}")

    # 2. Create the Admin User
    email = "admin@datavibe.io"
    password = "admin_password_123" # User should change this immediately
    full_name = "System Admin"
    
    hashed_pw = get_password_hash(password)
    
    try:
        con.execute("""
            INSERT INTO users (id, email, hashed_password, full_name, role, tenant_id)
            VALUES (?, ?, ?, ?, 'admin', ?)
        """, [uuid.uuid4().hex, email, hashed_pw, full_name, tenant_id])
        print(f"Successfully created admin user: {email}")
        print(f"Temporary Password: {password}")
        print("IMPORTANT: Change this password immediately after first login.")
    except Exception as e:
        if "UNIQUE constraint failed" in str(e):
            print(f"User {email} already exists.")
        else:
            print(f"Error creating user: {e}")
            
    con.close()

if __name__ == "__main__":
    seed_admin_user()
