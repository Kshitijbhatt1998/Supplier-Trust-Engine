import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.resolver import EntityResolver
from pipeline.storage.db import init_db

def test_negative_signal():
    con = init_db(":memory:")
    # Setup two suppliers that are potential matches for "Welpn"
    con.execute("INSERT INTO suppliers (id, name, country) VALUES ('welspun-india', 'Welspun India Limited', 'India')")
    con.execute("INSERT INTO suppliers (id, name, country) VALUES ('welspun-mills', 'Welspun Mills Pvt Ltd', 'India')")
    
    resolver = EntityResolver(con)
    name = "Welspn"
    norm = resolver.normalize(name)
    
    # 1. Initial Search: Should suggest Welspun India
    res1 = resolver.resolve(name, country='India')
    print(f"Initial Search: '{name}' -> Best candidate: {res1.get('canonical_name')} ({res1.get('match_score'):.1f})")
    
    # 2. Reject the first candidate
    print(f"\nRejecting {res1.get('canonical_name')}...")
    con.execute("""
        INSERT INTO entity_rejections (alias_normalized, canonical_id, reason_code)
        VALUES (?, ?, 'wrong_entity')
    """, [norm, res1.get('supplier_id')])
    
    # 3. Search Again: Should suggest the NEXT best (Welspun Mills)
    res2 = resolver.resolve(name, country='India')
    print(f"Search After Rejection: '{name}' -> Best candidate: {res2.get('canonical_name')} ({res2.get('match_score'):.1f})")
    
    if res2.get('supplier_id') == 'welspun-mills':
        print("PASS: System pivoted to the next best match.")
    else:
        print(f"FAIL: Unexpected candidate {res2.get('canonical_name')}")

    # 4. Reject the second candidate
    print(f"\nRejecting {res2.get('canonical_name')}...")
    con.execute("""
        INSERT INTO entity_rejections (alias_normalized, canonical_id, reason_code)
        VALUES (?, ?, 'wrong_entity')
    """, [norm, res2.get('supplier_id')])

    # 5. Search Again: Should have NO matches
    res3 = resolver.resolve(name, country='India')
    print(f"Search After All Rejections: '{name}' -> Supplier ID: {res3.get('supplier_id')}")
    
    if res3.get('supplier_id') is None:
        print("PASS: System returned No Match after all candidates rejected.")
    else:
        print("FAIL: Still showing matches after rejections.")

if __name__ == "__main__":
    test_negative_signal()
