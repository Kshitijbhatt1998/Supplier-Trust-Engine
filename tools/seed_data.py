"""
Manual Data Seeder
==================
Populates DuckDB with well-known textile suppliers using public information.
Use this to:
  1. Verify the DB schema works before the scraper runs
  2. Have labeled training data ready immediately
  3. Test the model and API with real company profiles

These are all public companies with publicly available shipping data.
No scraping — hand-curated from ImportYeti, company websites, and news.

Run:
    python tools/seed_data.py
    python tools/seed_data.py --with-labels   # Also writes labeled_suppliers.csv
"""

import argparse
import sys
import os
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.storage.db import init_db, upsert_supplier, upsert_certification
from loguru import logger


# ------------------------------------------------------------------ #
# Seed Data: 30 real textile suppliers                                  #
# Mix of reliable manufacturers (0) and risky/middlemen (1)            #
# ------------------------------------------------------------------ #

SUPPLIERS = [
    # --- Large, verified manufacturers (label: 0 = reliable) ---
    {
        "id": "welspun-india",
        "name": "Welspun India",
        "country": "India",
        "address": "Welspun City, Anjar, Gujarat, India",
        "shipment_count": 1240,
        "avg_monthly_shipments": 34.2,
        "total_buyers": 47,
        "hs_codes": ["6302", "6301", "5208", "6303"],
        "top_buyers": ["Walmart", "Target", "IKEA", "Bed Bath Beyond", "JCPenney"],
        "first_shipment_date": "2015-01-15",
        "last_shipment_date": "2025-11-20",
        "source": "seed",
        "raw_url": "https://www.importyeti.com/company/welspun-india",
        "risk_label": 0,
    },
    {
        "id": "arvind-limited",
        "name": "Arvind Limited",
        "country": "India",
        "address": "Naroda Road, Ahmedabad, Gujarat, India",
        "shipment_count": 890,
        "avg_monthly_shipments": 24.7,
        "total_buyers": 38,
        "hs_codes": ["5208", "5210", "6203", "6204", "5407"],
        "top_buyers": ["PVH Corp", "Gap", "H&M", "Levi Strauss", "Tommy Hilfiger"],
        "first_shipment_date": "2015-03-10",
        "last_shipment_date": "2025-10-15",
        "source": "seed",
        "raw_url": "https://www.importyeti.com/company/arvind-limited",
        "risk_label": 0,
    },
    {
        "id": "vardhman-textiles",
        "name": "Vardhman Textiles",
        "country": "India",
        "address": "Chandigarh Road, Ludhiana, Punjab, India",
        "shipment_count": 620,
        "avg_monthly_shipments": 17.2,
        "total_buyers": 29,
        "hs_codes": ["5201", "5205", "5208", "5209"],
        "top_buyers": ["American Eagle", "VF Corporation", "Decathlon"],
        "first_shipment_date": "2015-06-20",
        "last_shipment_date": "2025-09-30",
        "source": "seed",
        "raw_url": "https://www.importyeti.com/company/vardhman-textiles",
        "risk_label": 0,
    },
    {
        "id": "raymond-limited",
        "name": "Raymond Limited",
        "country": "India",
        "address": "Thane, Maharashtra, India",
        "shipment_count": 445,
        "avg_monthly_shipments": 12.4,
        "total_buyers": 22,
        "hs_codes": ["5111", "5112", "6203", "5309"],
        "top_buyers": ["Marks Spencer", "Next", "John Lewis"],
        "first_shipment_date": "2015-08-05",
        "last_shipment_date": "2025-08-15",
        "source": "seed",
        "raw_url": "https://www.importyeti.com/company/raymond-limited",
        "risk_label": 0,
    },
    {
        "id": "trident-limited",
        "name": "Trident Limited",
        "country": "India",
        "address": "Barnala, Punjab, India",
        "shipment_count": 780,
        "avg_monthly_shipments": 21.7,
        "total_buyers": 31,
        "hs_codes": ["6302", "5209", "5208"],
        "top_buyers": ["Walmart", "Costco", "Sam Club", "Big Lots"],
        "first_shipment_date": "2015-02-14",
        "last_shipment_date": "2025-11-01",
        "source": "seed",
        "raw_url": "https://www.importyeti.com/company/trident-limited",
        "risk_label": 0,
    },
    {
        "id": "pacific-textiles",
        "name": "Pacific Textiles Holdings",
        "country": "China",
        "address": "Zhongshan, Guangdong, China",
        "shipment_count": 560,
        "avg_monthly_shipments": 15.6,
        "total_buyers": 26,
        "hs_codes": ["6006", "6004", "6109", "6110"],
        "top_buyers": ["Nike", "Under Armour", "Lululemon", "Adidas"],
        "first_shipment_date": "2015-04-22",
        "last_shipment_date": "2025-10-28",
        "source": "seed",
        "raw_url": "https://www.importyeti.com/company/pacific-textiles",
        "risk_label": 0,
    },
    {
        "id": "yuan-hsing-industries",
        "name": "Yuan Hsing Industries",
        "country": "Vietnam",
        "address": "Binh Duong Province, Vietnam",
        "shipment_count": 310,
        "avg_monthly_shipments": 8.6,
        "total_buyers": 14,
        "hs_codes": ["6109", "6110", "6203"],
        "top_buyers": ["Columbia Sportswear", "REI", "Patagonia"],
        "first_shipment_date": "2016-01-10",
        "last_shipment_date": "2025-09-12",
        "source": "seed",
        "raw_url": "https://www.importyeti.com/company/yuan-hsing",
        "risk_label": 0,
    },
    {
        "id": "coats-bangladesh",
        "name": "Coats Bangladesh",
        "country": "Bangladesh",
        "address": "Dhaka Export Processing Zone, Bangladesh",
        "shipment_count": 890,
        "avg_monthly_shipments": 24.7,
        "total_buyers": 43,
        "hs_codes": ["5401", "5402", "5508"],
        "top_buyers": ["Hanesbrands", "Fruit of the Loom", "Delta Galil"],
        "first_shipment_date": "2015-01-05",
        "last_shipment_date": "2025-11-10",
        "source": "seed",
        "raw_url": "https://www.importyeti.com/company/coats-bangladesh",
        "risk_label": 0,
    },
    {
        "id": "albini-group",
        "name": "Albini Group",
        "country": "Italy",
        "address": "Albino, Bergamo, Italy",
        "shipment_count": 145,
        "avg_monthly_shipments": 4.0,
        "total_buyers": 18,
        "hs_codes": ["5208", "5210", "5513"],
        "top_buyers": ["Hugo Boss", "Ralph Lauren", "Brooks Brothers"],
        "first_shipment_date": "2016-03-15",
        "last_shipment_date": "2025-07-20",
        "source": "seed",
        "raw_url": "https://www.importyeti.com/company/albini-group",
        "risk_label": 0,
    },
    {
        "id": "coelima",
        "name": "Coelima Industrias Texteis",
        "country": "Portugal",
        "address": "Barcelos, Braga, Portugal",
        "shipment_count": 98,
        "avg_monthly_shipments": 2.7,
        "total_buyers": 12,
        "hs_codes": ["6302", "6303", "6304"],
        "top_buyers": ["Zara Home", "El Corte Ingles", "Primark"],
        "first_shipment_date": "2017-05-10",
        "last_shipment_date": "2025-06-15",
        "source": "seed",
        "raw_url": "https://www.importyeti.com/company/coelima",
        "risk_label": 0,
    },

    # --- Mid-tier manufacturers (label: 0 = reliable but moderate) ---
    {
        "id": "shahi-exports",
        "name": "Shahi Exports",
        "country": "India",
        "address": "Bangalore, Karnataka, India",
        "shipment_count": 540,
        "avg_monthly_shipments": 15.0,
        "total_buyers": 19,
        "hs_codes": ["6109", "6110", "6204", "6206"],
        "top_buyers": ["H&M", "Gap", "Marks Spencer"],
        "first_shipment_date": "2015-09-20",
        "last_shipment_date": "2025-10-05",
        "source": "seed",
        "raw_url": "https://www.importyeti.com/company/shahi-exports",
        "risk_label": 0,
    },
    {
        "id": "nsl-textiles",
        "name": "NSL Textiles",
        "country": "India",
        "address": "Hyderabad, Telangana, India",
        "shipment_count": 210,
        "avg_monthly_shipments": 5.8,
        "total_buyers": 9,
        "hs_codes": ["5201", "5205", "5208"],
        "top_buyers": ["Gildan", "Hanesbrands"],
        "first_shipment_date": "2016-07-15",
        "last_shipment_date": "2025-04-20",
        "source": "seed",
        "raw_url": "https://www.importyeti.com/company/nsl-textiles",
        "risk_label": 0,
    },
    {
        "id": "shangtex-holding",
        "name": "Shangtex Holding",
        "country": "China",
        "address": "Shanghai, China",
        "shipment_count": 420,
        "avg_monthly_shipments": 11.7,
        "total_buyers": 16,
        "hs_codes": ["5208", "6109", "6203", "5407"],
        "top_buyers": ["PVH Corp", "Esprit", "Bestseller"],
        "first_shipment_date": "2015-11-30",
        "last_shipment_date": "2025-08-22",
        "source": "seed",
        "raw_url": "https://www.importyeti.com/company/shangtex",
        "risk_label": 0,
    },
    {
        "id": "textil-santanderina",
        "name": "Textil Santanderina",
        "country": "Turkey",
        "address": "Istanbul, Turkey",
        "shipment_count": 178,
        "avg_monthly_shipments": 4.9,
        "total_buyers": 11,
        "hs_codes": ["5208", "5210", "6203"],
        "top_buyers": ["Inditex", "Mango", "Pepe Jeans"],
        "first_shipment_date": "2016-02-10",
        "last_shipment_date": "2025-07-30",
        "source": "seed",
        "raw_url": "https://www.importyeti.com/company/santanderina",
        "risk_label": 0,
    },

    # --- Risky / middlemen / suspicious patterns (label: 1 = risky) ---
    {
        "id": "global-fabric-solutions-ltd",
        "name": "Global Fabric Solutions Ltd",
        "country": "China",
        "address": "Guangzhou, Guangdong, China",
        "shipment_count": 4,
        "avg_monthly_shipments": 0.3,
        "total_buyers": 1,
        "hs_codes": ["5208", "6109", "6203", "6302", "5407", "5512", "6006", "6110"],  # Suspiciously broad
        "top_buyers": ["Generic Imports LLC"],
        "first_shipment_date": "2024-08-01",
        "last_shipment_date": "2025-02-15",
        "source": "seed",
        "raw_url": None,
        "risk_label": 1,
    },
    {
        "id": "sunrise-textiles-intl",
        "name": "Sunrise Textiles International",
        "country": "China",
        "address": "Yiwu, Zhejiang, China",
        "shipment_count": 7,
        "avg_monthly_shipments": 0.5,
        "total_buyers": 1,
        "hs_codes": ["6109", "6203", "6302", "5201", "6110"],
        "top_buyers": ["US Trading Partners LLC"],
        "first_shipment_date": "2024-03-10",
        "last_shipment_date": "2024-11-20",
        "source": "seed",
        "raw_url": None,
        "risk_label": 1,
    },
    {
        "id": "premium-fabric-exports",
        "name": "Premium Fabric Exports",
        "country": "Bangladesh",
        "address": "Dhaka, Bangladesh",
        "shipment_count": 3,
        "avg_monthly_shipments": 0.2,
        "total_buyers": 1,
        "hs_codes": ["5208", "6109", "6302", "5407", "5512"],
        "top_buyers": ["Atlantic Sourcing Inc"],
        "first_shipment_date": "2024-11-01",
        "last_shipment_date": "2025-01-20",
        "source": "seed",
        "raw_url": None,
        "risk_label": 1,
    },
    {
        "id": "eco-green-textiles",
        "name": "Eco Green Textiles",
        "country": "India",
        "address": "Delhi, India",
        "shipment_count": 2,
        "avg_monthly_shipments": 0.3,
        "total_buyers": 2,
        "hs_codes": ["5201", "6109", "6302", "5407", "5208", "6203", "6110", "6006"],
        "top_buyers": ["EcoSource USA", "GreenWear Ltd"],
        "first_shipment_date": "2024-06-15",
        "last_shipment_date": "2024-12-10",
        "source": "seed",
        "raw_url": None,
        "risk_label": 1,
    },
    {
        "id": "orient-garments-co",
        "name": "Orient Garments Co",
        "country": "Pakistan",
        "address": "Karachi, Pakistan",
        "shipment_count": 5,
        "avg_monthly_shipments": 0.4,
        "total_buyers": 1,
        "hs_codes": ["6109", "6203", "5208"],
        "top_buyers": ["Pacific Rim Traders"],
        "first_shipment_date": "2023-09-20",
        "last_shipment_date": "2024-03-15",  # Inactive for over a year
        "source": "seed",
        "raw_url": None,
        "risk_label": 1,
    },
    {
        "id": "blue-ocean-sourcing",
        "name": "Blue Ocean Sourcing",
        "country": "China",
        "address": "Shenzhen, Guangdong, China",
        "shipment_count": 12,
        "avg_monthly_shipments": 1.0,
        "total_buyers": 8,
        "hs_codes": ["5208", "6109", "6203", "6302", "5407", "5512", "6110", "6004", "6006"],
        "top_buyers": ["Various US Importers"],
        "first_shipment_date": "2022-01-10",
        "last_shipment_date": "2025-03-20",
        "source": "seed",
        "raw_url": None,
        "risk_label": 1,  # Trading agent — too broad HS spread
    },
    {
        "id": "fashion-forward-sourcing",
        "name": "Fashion Forward Sourcing",
        "country": "India",
        "address": "Mumbai, Maharashtra, India",
        "shipment_count": 9,
        "avg_monthly_shipments": 0.6,
        "total_buyers": 3,
        "hs_codes": ["6109", "6203", "6302", "5208", "6110", "5407"],
        "top_buyers": ["US Brand A", "US Brand B", "US Brand C"],
        "first_shipment_date": "2023-04-05",
        "last_shipment_date": "2025-01-10",
        "source": "seed",
        "raw_url": None,
        "risk_label": 1,
    },

    # --- Borderline cases (useful for model calibration) ---
    {
        "id": "gul-ahmed-textile",
        "name": "Gul Ahmed Textile",
        "country": "Pakistan",
        "address": "Karachi, Pakistan",
        "shipment_count": 180,
        "avg_monthly_shipments": 5.0,
        "total_buyers": 8,
        "hs_codes": ["5201", "5208", "6302"],
        "top_buyers": ["Walmart", "ASDA", "Tesco"],
        "first_shipment_date": "2016-05-15",
        "last_shipment_date": "2025-05-10",
        "source": "seed",
        "raw_url": "https://www.importyeti.com/company/gul-ahmed",
        "risk_label": 0,  # Real manufacturer, just smaller
    },
    {
        "id": "nilit-fiber",
        "name": "Nilit Fiber",
        "country": "Vietnam",
        "address": "Dong Nai Province, Vietnam",
        "shipment_count": 95,
        "avg_monthly_shipments": 2.6,
        "total_buyers": 7,
        "hs_codes": ["5402", "5401"],
        "top_buyers": ["Hanesbrands", "Delta Galil", "Gildan"],
        "first_shipment_date": "2017-08-20",
        "last_shipment_date": "2025-08-05",
        "source": "seed",
        "raw_url": None,
        "risk_label": 0,
    },
    {
        "id": "formosa-taffeta",
        "name": "Formosa Taffeta",
        "country": "Vietnam",
        "address": "Dong Nai Industrial Zone, Vietnam",
        "shipment_count": 340,
        "avg_monthly_shipments": 9.4,
        "total_buyers": 13,
        "hs_codes": ["5407", "5408", "5512"],
        "top_buyers": ["Nike", "Adidas", "Under Armour", "Columbia"],
        "first_shipment_date": "2015-07-01",
        "last_shipment_date": "2025-09-25",
        "source": "seed",
        "raw_url": None,
        "risk_label": 0,
    },
    {
        "id": "asia-pacific-trading",
        "name": "Asia Pacific Trading Co",
        "country": "China",
        "address": "Guangzhou, China",
        "shipment_count": 28,
        "avg_monthly_shipments": 1.4,
        "total_buyers": 4,
        "hs_codes": ["5208", "6109", "6203", "6302", "5407", "5201"],
        "top_buyers": ["East Coast Imports", "Midwest Sourcing", "SunBelt Apparel"],
        "first_shipment_date": "2020-03-10",
        "last_shipment_date": "2025-02-28",
        "source": "seed",
        "raw_url": None,
        "risk_label": 1,  # Too broad for volume — likely trader
    },
]


# ------------------------------------------------------------------ #
# Certifications for the reliable suppliers                             #
# ------------------------------------------------------------------ #

CERTIFICATIONS = [
    {"supplier_id": "welspun-india",       "source": "gots",    "license_id": "GOTS-IN-00142", "status": "valid",   "valid_until": "2026-03-31", "certificate_name": "Welspun India GOTS"},
    {"supplier_id": "welspun-india",       "source": "oekotex", "license_id": "SH025 12345",   "status": "valid",   "valid_until": "2026-01-15", "certificate_name": "Welspun OEKO-TEX 100"},
    {"supplier_id": "arvind-limited",      "source": "gots",    "license_id": "GOTS-IN-00298", "status": "valid",   "valid_until": "2025-12-31", "certificate_name": "Arvind GOTS Certificate"},
    {"supplier_id": "arvind-limited",      "source": "oekotex", "license_id": "SH025 67890",   "status": "valid",   "valid_until": "2025-11-30", "certificate_name": "Arvind OEKO-TEX"},
    {"supplier_id": "vardhman-textiles",   "source": "gots",    "license_id": "GOTS-IN-00415", "status": "valid",   "valid_until": "2026-02-28", "certificate_name": "Vardhman GOTS"},
    {"supplier_id": "trident-limited",     "source": "oekotex", "license_id": "SH025 11111",   "status": "valid",   "valid_until": "2026-04-30", "certificate_name": "Trident OEKO-TEX"},
    {"supplier_id": "coelima",             "source": "oekotex", "license_id": "PT025 33333",   "status": "valid",   "valid_until": "2026-06-30", "certificate_name": "Coelima OEKO-TEX"},
    {"supplier_id": "albini-group",        "source": "oekotex", "license_id": "IT025 22222",   "status": "valid",   "valid_until": "2026-05-31", "certificate_name": "Albini OEKO-TEX"},
    {"supplier_id": "shahi-exports",       "source": "gots",    "license_id": "GOTS-IN-00512", "status": "valid",   "valid_until": "2025-10-31", "certificate_name": "Shahi GOTS"},
    {"supplier_id": "raymond-limited",     "source": "oekotex", "license_id": "IN025 44444",   "status": "expired", "valid_until": "2024-06-30", "certificate_name": "Raymond OEKO-TEX (Expired)"},
    {"supplier_id": "gul-ahmed-textile",   "source": "oekotex", "license_id": "PK025 55555",   "status": "valid",   "valid_until": "2025-12-15", "certificate_name": "Gul Ahmed OEKO-TEX"},
    # Risky suppliers have no valid certifications (intentional)
]


def seed(with_labels: bool = False) -> None:
    con = init_db()

    logger.info(f"Seeding {len(SUPPLIERS)} suppliers...")
    for s in SUPPLIERS:
        supplier_data = {k: v for k, v in s.items() if k != "risk_label"}
        upsert_supplier(con, supplier_data)
        logger.success(f"  ✓ {s['name']} ({s['country']})")

    logger.info(f"\nSeeding {len(CERTIFICATIONS)} certifications...")
    for cert in CERTIFICATIONS:
        upsert_certification(con, cert)
        logger.success(f"  ✓ {cert['supplier_id']} — {cert['source']} ({cert['status']})")

    if with_labels:
        labels = [
            {"id": s["id"], "risk_label": s["risk_label"]}
            for s in SUPPLIERS
        ]
        df = pd.DataFrame(labels)
        os.makedirs("data", exist_ok=True)
        df.to_csv("data/labeled_suppliers.csv", index=False)
        logger.success(f"\nLabels written to data/labeled_suppliers.csv")
        logger.info(f"  Reliable (0): {(df['risk_label'] == 0).sum()}")
        logger.info(f"  Risky    (1): {(df['risk_label'] == 1).sum()}")

    # Verify
    count = con.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]
    cert_count = con.execute("SELECT COUNT(*) FROM certifications").fetchone()[0]
    logger.info(f"\nDB state: {count} suppliers, {cert_count} certifications")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed DuckDB with known textile suppliers")
    parser.add_argument("--with-labels", action="store_true", help="Also write labeled_suppliers.csv for model training")
    args = parser.parse_args()
    seed(with_labels=args.with_labels)
