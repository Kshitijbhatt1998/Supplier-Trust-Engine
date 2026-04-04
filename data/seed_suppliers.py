"""
Synthetic Seed Data Generator
==============================
Generates 50 realistic synthetic textile suppliers and seeds them into DuckDB.
Also writes data/labeled_suppliers.csv with binary risk labels for model training.

Run from project root:
    python data/seed_suppliers.py
    # or via pipeline:
    python run_pipeline.py --seed
"""

import os
import sys
import csv
import random
from datetime import date, timedelta

# Allow running from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from pipeline.storage.db import init_db, upsert_supplier, upsert_certification
from loguru import logger

random.seed(42)


# ------------------------------------------------------------------ #
# 50 Supplier catalogue — realistic textile manufacturers               #
# ------------------------------------------------------------------ #

SUPPLIERS_RAW = [
    # (name, country, risk_label)  0 = reliable manufacturer, 1 = risky middleman
    # --- Reliable Tier-1 Manufacturers (30) ---
    ("Welspun India Ltd",            "India",      0),
    ("Arvind Limited",               "India",      0),
    ("Vardhman Textiles",            "India",      0),
    ("Trident Group",                "India",      0),
    ("Raymond Textiles",             "India",      0),
    ("Alok Industries",              "India",      0),
    ("KPR Mill Limited",             "India",      0),
    ("Himatsingka Seide",            "India",      0),
    ("Bombay Dyeing",                "India",      0),
    ("Indo Count Industries",        "India",      0),
    ("Luthai Textile Co",            "China",      0),
    ("Huafu Fashion Co",             "China",      0),
    ("Texhong Textile Group",        "China",      0),
    ("Shandong Ruyi Group",          "China",      0),
    ("Jingwei Textile Machinery",    "China",      0),
    ("Pakson International",         "Turkey",     0),
    ("Kipas Holding",                "Turkey",     0),
    ("Sanko Tekstil",                "Turkey",     0),
    ("Yunsa Yunlu",                  "Turkey",     0),
    ("Bossa Ticaret",                "Turkey",     0),
    ("Coats Bangladesh",             "Bangladesh", 0),
    ("DBL Group",                    "Bangladesh", 0),
    ("Square Textiles",              "Bangladesh", 0),
    ("Pacific Jeans",                "Bangladesh", 0),
    ("BEXIMCO Textiles",             "Bangladesh", 0),
    ("Coelho da Fonseca",            "Portugal",   0),
    ("Somelos Tecidos",              "Portugal",   0),
    ("Impetus Portugal",             "Portugal",   0),
    ("Sitip SpA",                    "Italy",      0),
    ("Filatura Biagioli Modesto",    "Italy",      0),
    # --- Risky / Middleman Brokers (20) ---
    ("Global Textile Brokers LLC",   "China",      1),
    ("Apex Trading International",   "China",      1),
    ("Premier Sourcing Group",       "Bangladesh", 1),
    ("EuroTex Trading GmbH",         "Germany",    1),
    ("FastFashion Supplies Co",      "India",      1),
    ("HK Textile Exports Ltd",       "China",      1),
    ("QuickSource Partners",         "Pakistan",   1),
    ("Dynasty Export House",         "India",      1),
    ("Sunbright International",      "China",      1),
    ("Pacific Rim Traders",          "Vietnam",    1),
    ("Alpha Apparel Agents",         "Bangladesh", 1),
    ("Continental Textile Agency",   "China",      1),
    ("Prime Fabric Solutions",       "India",      1),
    ("SwiftStitch Brokers",          "Pakistan",   1),
    ("Nexus Garment Group",          "China",      1),
    ("Horizon Trade Links",          "India",      1),
    ("Metro Textile Agency",         "Bangladesh", 1),
    ("Star Export Alliance",         "China",      1),
    ("Noble Trading Co",             "Vietnam",    1),
    ("First Class Fabrics Ltd",      "India",      1),
]

HS_CODES = {
    "woven":   ["5208", "5209", "5210", "5212", "5407", "5408"],
    "knit":    ["6001", "6002", "6003", "6004", "6005", "6006"],
    "apparel": ["6101", "6102", "6103", "6104", "6109", "6110"],
    "home":    ["6301", "6302", "6303", "6304", "6305", "6307"],
    "yarn":    ["5201", "5204", "5205", "5206", "5207"],
}

BUYERS = {
    "India":       ["H&M", "Zara", "Next", "Marks & Spencer", "Walmart", "Target", "IKEA"],
    "China":       ["Primark", "SHEIN", "Uniqlo", "Gap Inc", "Foot Locker", "PVH Corp"],
    "Bangladesh":  ["H&M", "Primark", "Zara", "C&A", "Kohls", "JCPenney"],
    "Turkey":      ["Zara", "H&M", "Mango", "DeFacto", "LC Waikiki", "Next"],
    "Portugal":    ["Burberry", "Hugo Boss", "Armani", "Mango", "Massimo Dutti"],
    "Italy":       ["Gucci", "Prada", "Versace", "Armani", "Dolce Gabbana"],
    "Vietnam":     ["Nike", "Adidas", "VF Corp", "Columbia Sportswear"],
    "Pakistan":    ["Next", "Primark", "ASDA", "Tesco", "Target"],
    "Germany":     ["Zalando", "Otto Group", "Hugo Boss", "Tom Tailor"],
}


def _slug(name: str) -> str:
    """Convert company name to URL-safe slug ID."""
    return (
        name.lower()
        .replace(" ", "-")
        .replace("&", "and")
        .replace(".", "")
        .replace(",", "")
        .replace("'", "")
        [:50]
    )


def _build_supplier(name: str, country: str, risk_label: int) -> dict:
    """Build a realistic synthetic supplier profile."""
    slug = _slug(name)
    is_reliable = (risk_label == 0)

    if is_reliable:
        # Real manufacturers: many shipments, many buyers, narrowly focused HS codes
        shipment_count = random.randint(80, 800)
        avg_monthly    = round(random.uniform(6.0, 40.0), 1)
        total_buyers   = random.randint(5, 25)
        years_active   = random.uniform(4.0, 18.0)
        category       = random.choice(list(HS_CODES.keys()))
        hs_codes       = random.sample(HS_CODES[category], k=random.randint(2, 4))
        days_inactive  = random.randint(5, 60)
    else:
        # Middlemen: low volume, few buyers, scattered HS codes across many chapters
        shipment_count = random.randint(3, 60)
        avg_monthly    = round(random.uniform(0.2, 5.0), 1)
        total_buyers   = random.randint(1, 4)
        years_active   = random.uniform(0.5, 5.0)
        hs_codes = []
        for cat in random.sample(list(HS_CODES.keys()), k=random.randint(3, 5)):
            hs_codes += random.sample(HS_CODES[cat], k=1)
        days_inactive  = random.randint(0, 500)

    first_date  = date.today() - timedelta(days=int(years_active * 365))
    last_date   = date.today() - timedelta(days=days_inactive)
    buyers_pool = BUYERS.get(country, ["Unknown Buyer"])
    top_buyers  = random.sample(buyers_pool, k=min(total_buyers, len(buyers_pool)))

    return {
        "id":                    slug,
        "name":                  name,
        "country":               country,
        "address":               f"Industrial Zone, {country}",
        "shipment_count":        shipment_count,
        "avg_monthly_shipments": avg_monthly,
        "total_buyers":          total_buyers,
        "hs_codes":              hs_codes,
        "top_buyers":            top_buyers,
        "first_shipment_date":   first_date.isoformat(),
        "last_shipment_date":    last_date.isoformat(),
        "source":                "seed",
        "raw_url":               f"https://www.importyeti.com/company/{slug}",
    }


def _build_certifications(supplier_id: str, risk_label: int) -> list[dict]:
    """Reliable suppliers get valid GOTS/OEKO-TEX; risky ones usually have nothing."""
    certs = []
    if risk_label == 0:
        # 80% chance of valid GOTS
        if random.random() < 0.80:
            certs.append({
                "supplier_id":      supplier_id,
                "source":           "gots",
                "license_id":       f"GOTS-{random.randint(10000, 99999)}",
                "status":           "valid",
                "valid_until":      (date.today() + timedelta(days=random.randint(90, 730))).isoformat(),
                "certificate_name": "Global Organic Textile Standard",
            })
        # 90% chance of valid OEKO-TEX
        if random.random() < 0.90:
            certs.append({
                "supplier_id":      supplier_id,
                "source":           "oekotex",
                "license_id":       f"OT-{random.randint(100000, 999999)}",
                "status":           "valid",
                "valid_until":      (date.today() + timedelta(days=random.randint(30, 365))).isoformat(),
                "certificate_name": "OEKO-TEX Standard 100",
            })
    else:
        # 20% chance of a lapsed/expired cert
        if random.random() < 0.20:
            certs.append({
                "supplier_id":      supplier_id,
                "source":           random.choice(["gots", "oekotex"]),
                "license_id":       f"EXP-{random.randint(10000, 99999)}",
                "status":           "expired",
                "valid_until":      (date.today() - timedelta(days=random.randint(30, 500))).isoformat(),
                "certificate_name": "Expired Certification",
            })

    return certs


def generate_and_seed() -> None:
    """Seed DuckDB with synthetic suppliers + write labeled CSV."""
    os.makedirs("data", exist_ok=True)
    con = init_db()

    labels = []
    seeded = 0

    logger.info(f"Seeding {len(SUPPLIERS_RAW)} synthetic suppliers into DuckDB...")

    for name, country, risk_label in SUPPLIERS_RAW:
        supplier = _build_supplier(name, country, risk_label)

        try:
            upsert_supplier(con, supplier)
        except Exception as e:
            logger.warning(f"  Supplier insert failed [{name}]: {e}")
            continue

        for cert in _build_certifications(supplier["id"], risk_label):
            try:
                upsert_certification(con, cert)
            except Exception as e:
                logger.warning(f"  Cert insert failed [{name}]: {e}")

        labels.append({"id": supplier["id"], "risk_label": risk_label})
        seeded += 1
        logger.debug(f"  {'✓ reliable' if risk_label == 0 else '⚠ risky  '} {name} ({country})")

    # Write labeled CSV for model training
    label_csv = "data/labeled_suppliers.csv"
    with open(label_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "risk_label"])
        writer.writeheader()
        writer.writerows(labels)

    # Summary
    n_sup   = con.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]
    n_cert  = con.execute("SELECT COUNT(*) FROM certifications").fetchone()[0]
    n_valid = con.execute("SELECT COUNT(*) FROM certifications WHERE status='valid'").fetchone()[0]

    logger.success(f"Seeded {seeded}/{len(SUPPLIERS_RAW)} suppliers")
    logger.success(f"DB totals  → {n_sup} suppliers | {n_cert} certs ({n_valid} valid)")
    logger.success(f"Label CSV  → {label_csv}  ({len(labels)} rows, "
                   f"{sum(1 for r in labels if r['risk_label']==0)} reliable / "
                   f"{sum(1 for r in labels if r['risk_label']==1)} risky)")


if __name__ == "__main__":
    generate_and_seed()
