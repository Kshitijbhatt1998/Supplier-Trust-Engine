import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.resolver import EntityResolver
from pipeline.storage.db import init_db

def test_resolution():
    con = init_db(":memory:")
    
    # 1. Setup test data
    con.execute("INSERT INTO suppliers (id, name, country) VALUES ('welspun-india', 'Welspun India Limited', 'India')")
    
    resolver = EntityResolver(con)
    
    # 2. Test Normalization
    print("Testing Normalization:")
    pairs = [
        ("WELSPUN INDIA LIMITED", "india welspun"),
        ("Welspun Pvt Ltd", "welspun"),
        ("India Welspun", "india welspun"),
        ("Welspun EXIM Industries", "welspun"),
    ]
    for raw, expected in pairs:
        norm = resolver.normalize(raw)
        print(f"  '{raw}' -> '{norm}' {'[OK]' if norm == expected else '[FAIL]'}")

    # 3. Test Full Resolution
    print("\nTesting Resolution:")
    queries = [
        ("Welspun", "welspun-india"),
        ("Welspun India", "welspun-india"),
        ("India Welspun", "welspun-india"),
    ]
    for q_name, expected_id in queries:
        res_id, score, verified = resolver.resolve(q_name, country='India')
        print(f"  Query: '{q_name}' -> Resolved ID: {res_id} (Score: {score:.1f}, Verified: {verified}) {'[OK]' if res_id == expected_id else '[FAIL]'}")

    # 4. Test Fast Path (Cache)
    print("\nTesting Fast Path (Cache):")
    res_id, score, verified = resolver.resolve("Welspun", country='India')
    print(f"  Second Query: 'Welspun' -> {res_id} (Score: {score:.1f})")
    
    # Check if registered in entity_aliases
    alias = con.execute("SELECT * FROM entity_aliases WHERE alias_normalized = 'welspun'").fetchone()
    print(f"  Alias record exists in DB: {alias is not None}")

if __name__ == "__main__":
    test_resolution()
