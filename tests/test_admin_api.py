import sys
import os
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from api.main import app
from pipeline.storage.db import init_db

client = TestClient(app)

def test_admin_priority_and_actions():
    # 1. Setup Data
    con = init_db(":memory:")
    # We need to monkeypatch the app's db connection if it's not using a global 'con'
    # In main.py, 'con' is a global at the module level.
    import api.main
    api.main.con = con
    
    # Insert Supplier A (High Trust)
    con.execute("INSERT INTO suppliers (id, name, country) VALUES ('s-high', 'High Trust Corp', 'India')")
    con.execute("INSERT INTO trust_scores (supplier_id, trust_score) VALUES ('s-high', 95)")
    
    # Insert Supplier B (Low Trust)
    con.execute("INSERT INTO suppliers (id, name, country) VALUES ('s-low', 'Low Trust Corp', 'India')")
    con.execute("INSERT INTO trust_scores (supplier_id, trust_score) VALUES ('s-low', 40)")
    
    # Insert Alias 1 for B: Low Trust, High Volume (150 hits), Low Match (70)
    # Note P = (0.4 * cap(V, 100)/100) + (0.3 * T/100) + (0.3 * S/100)
    # V_norm = 1.0 (capped at 100), T_norm = 0.4, S_norm = 0.7 => P = 0.4 + 0.12 + 0.21 = 0.73
    con.execute("""
        INSERT INTO entity_aliases (id, alias_name, alias_normalized, canonical_id, match_score, suggestion_count, is_verified)
        VALUES ('a1', 'LowHit', 'lowhit', 's-low', 70.0, 150, FALSE)
    """)
    
    # Insert Alias 2 for A: High Trust, Low Volume (10 hits), High Match (95)
    # V_norm = 0.1, T_norm = 0.95, S_norm = 0.95 => P = 0.04 + 0.285 + 0.285 = 0.61
    con.execute("""
        INSERT INTO entity_aliases (id, alias_name, alias_normalized, canonical_id, match_score, suggestion_count, is_verified)
        VALUES ('a2', 'HighHit', 'highhit', 's-high', 95.0, 10, FALSE)
    """)
    
    headers = {"X-Admin-Token": "dev-admin-pass-123"}
    
    # 2. Check Review Queue Priority
    response = client.get("/v1/admin/review-queue", headers=headers)
    assert response.status_code == 200
    data = response.json()
    
    print("\nPriority Queue Debug:")
    for item in data:
        print(f"ID: {item['id']} | P: {item['priority_score']} | T: {item['trust_score']} | S: {item['match_score']} | V: {item['suggestion_count']}")
        
    assert data[0]['id'] == 'a1', "Top item should be 'a1' due to capped volume priority"
    
    # 3. Test Verify Action
    action_res = client.post("/v1/admin/alias/action", headers=headers, json={
        "alias_ids": ["a1"],
        "action": "verify"
    })
    assert action_res.status_code == 200
    
    # Verify it's gone from queue
    queue_after = client.get("/v1/admin/review-queue", headers=headers).json()
    assert len(queue_after) == 1
    assert queue_after[0]['id'] == 'a2'
    
    # 4. Test Reject Action
    reject_res = client.post("/v1/admin/alias/action", headers=headers, json={
        "alias_ids": ["a2"],
        "action": "reject",
        "reason_code": "wrong_entity"
    })
    assert reject_res.status_code == 200
    
    # Verify it moved to rejections
    rej_row = con.execute("SELECT * FROM entity_rejections").fetchone()
    assert rej_row is not None
    assert rej_row[1] == 's-high'
    
    # 5. Unauthorized Check
    bad_res = client.get("/v1/admin/review-queue", headers={"X-Admin-Token": "wrong"})
    assert bad_res.status_code == 403

    print("\nAdmin API Tests PASSED")

if __name__ == "__main__":
    test_admin_priority_and_actions()
