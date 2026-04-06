import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.resolver import EntityResolver
from pipeline.storage.db import init_db

def test_ghost_cache_and_feedback():
    con = init_db(":memory:")
    con.execute("INSERT INTO suppliers (id, name, country) VALUES ('welspun-india', 'Welspun India Limited', 'India')")
    resolver = EntityResolver(con)
    
    # 1. Low Confidence Query (e.g. "Welpn")
    name = "Welpn"
    res = resolver.resolve(name, country='India')
    print(f"Query: '{name}' -> Score: {res.get('match_score'):.1f}, Low Conf: {res.get('low_confidence')}")
    
    # 2. Check if cached (Should NOT be)
    norm = resolver.normalize(name)
    alias = con.execute("SELECT * FROM entity_aliases WHERE alias_normalized = ?", [norm]).fetchone()
    print(f"Cached in DB? {'Yes (FAIL)' if alias else 'No (PASS - Ghost Cache Prevented)'}")

    # 3. Simulate HITL Feedback (manual registration)
    if res.get('supplier_id'):
        print("\nSimulating HITL Feedback...")
        resolver._register_alias(name, norm, res.get('supplier_id'), res.get('match_score'), False)
        
        # 4. Check if cached now
        alias = con.execute("SELECT * FROM entity_aliases WHERE alias_normalized = ?", [norm]).fetchone()
        print(f"Cached in DB after feedback? {'Yes (PASS)' if alias else 'No (FAIL)'}")
        
if __name__ == "__main__":
    test_ghost_cache_and_feedback()
