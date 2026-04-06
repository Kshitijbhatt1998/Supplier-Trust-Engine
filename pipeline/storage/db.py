"""
DuckDB storage layer for the Textile Supplier Trust Engine.
All raw scraped data lands here before the model sees it.
"""

import duckdb
import os
from loguru import logger
from typing import Optional


def get_db_path() -> str:
    return os.getenv("DB_PATH", "data/trust_engine.duckdb")


def init_db(path: Optional[str] = None) -> duckdb.DuckDBPyConnection:
    """Initialize DuckDB with all required tables."""
    db_path = path or get_db_path()
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    con = duckdb.connect(db_path)

    con.execute("""
        CREATE TABLE IF NOT EXISTS suppliers (
            id                  VARCHAR PRIMARY KEY,      -- slugified company name
            name                VARCHAR NOT NULL,
            country             VARCHAR,
            address             VARCHAR,
            shipment_count      INTEGER,
            avg_monthly_shipments FLOAT,
            total_buyers        INTEGER,
            hs_codes            VARCHAR[],                -- e.g. ['5201', '6109']
            top_buyers          VARCHAR[],                -- customer concentration signal
            first_shipment_date DATE,
            last_shipment_date  DATE,
            source              VARCHAR,                  -- 'importyeti' | 'indiamart' | ...
            raw_url             VARCHAR,
            scraped_at          TIMESTAMP DEFAULT NOW()
        );
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS certifications (
            id              VARCHAR PRIMARY KEY,          -- supplier_id + ':' + source
            supplier_id     VARCHAR REFERENCES suppliers(id),
            license_id      VARCHAR,
            source          VARCHAR,                      -- 'oekotex' | 'gots' | 'grs'
            status          VARCHAR,                      -- 'valid' | 'expired' | 'not_found'
            valid_until     DATE,
            certificate_name VARCHAR,
            verified_at     TIMESTAMP DEFAULT NOW()
        );
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS shipments (
            id              VARCHAR PRIMARY KEY,
            supplier_id     VARCHAR REFERENCES suppliers(id),
            shipment_date   DATE,
            weight_kg       FLOAT,
            volume_teu      FLOAT,                        -- twenty-foot equivalent units
            hs_code         VARCHAR,
            origin_port     VARCHAR,
            destination_port VARCHAR,
            consignee       VARCHAR,                      -- buyer name
            bill_of_lading  VARCHAR,
            scraped_at      TIMESTAMP DEFAULT NOW()
        );
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS trust_scores (
            supplier_id         VARCHAR PRIMARY KEY REFERENCES suppliers(id),
            trust_score         FLOAT,                    -- 0-100
            risk_label          INTEGER,                  -- 0=reliable, 1=risky
            feature_json        VARCHAR,                  -- JSON blob of features
            shap_flags_json     VARCHAR,                  -- top risk flags from SHAP
            scored_at           TIMESTAMP DEFAULT NOW()
        );
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS trade_stats (
            id                  VARCHAR PRIMARY KEY,      -- reporter_code:partner_code:year:hs_code
            reporter_code       VARCHAR,
            partner_code        VARCHAR,
            year                INTEGER,
            hs_code             VARCHAR,
            trade_value_usd     FLOAT,
            net_weight_kg       FLOAT,
            updated_at          TIMESTAMP DEFAULT NOW()
        );
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS entity_aliases (
            id                  VARCHAR PRIMARY KEY,      -- sha256[:20] of lowercased raw_name
            alias_name          VARCHAR NOT NULL,         -- raw name as seen on source
            alias_normalized    VARCHAR NOT NULL,         -- normalized form (token-sorted)
            canonical_id        VARCHAR,                  -- FK omitted: alias may be registered
                                                          -- before the supplier row is inserted
            source              VARCHAR,                  -- 'importyeti' | 'bol' | 'indiamart' | ...
            match_score         FLOAT,                    -- 0–100; 100=exact/alias, 0=new entity
            is_verified         BOOLEAN DEFAULT FALSE,    -- manual/trusted match flag
            resolved_at         TIMESTAMP DEFAULT NOW()
        );
    """)

    # Indexes on hot query paths
    con.execute("CREATE INDEX IF NOT EXISTS idx_trust_score      ON trust_scores(trust_score)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_trust_supplier   ON trust_scores(supplier_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_supplier_country ON suppliers(country)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_cert_supplier    ON certifications(supplier_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_cert_status      ON certifications(status)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_alias_norm       ON entity_aliases(alias_normalized)")

    logger.info(f"Database initialized at {db_path}")
    return con


def upsert_supplier(con: duckdb.DuckDBPyConnection, supplier: dict) -> None:
    """Insert or update a supplier record.

    DuckDB 0.x does not support updating VARCHAR[] (list) columns via
    ON CONFLICT DO UPDATE, so we INSERT on first seen and UPDATE scalar
    stats on re-scrape. hs_codes/top_buyers are stable per supplier and
    are only written on the initial insert.
    """
    try:
        con.execute("""
            INSERT INTO suppliers (
                id, name, country, address, shipment_count,
                avg_monthly_shipments, total_buyers, hs_codes,
                top_buyers, first_shipment_date, last_shipment_date,
                source, raw_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            supplier.get("id"),
            supplier.get("name"),
            supplier.get("country"),
            supplier.get("address"),
            supplier.get("shipment_count"),
            supplier.get("avg_monthly_shipments"),
            supplier.get("total_buyers"),
            supplier.get("hs_codes", []),
            supplier.get("top_buyers", []),
            supplier.get("first_shipment_date"),
            supplier.get("last_shipment_date"),
            supplier.get("source"),
            supplier.get("raw_url"),
        ])
    except duckdb.ConstraintException:
        con.execute("""
            UPDATE suppliers SET
                shipment_count        = ?,
                avg_monthly_shipments = ?,
                total_buyers          = ?,
                last_shipment_date    = ?,
                scraped_at            = NOW()
            WHERE id = ?
        """, [
            supplier.get("shipment_count"),
            supplier.get("avg_monthly_shipments"),
            supplier.get("total_buyers"),
            supplier.get("last_shipment_date"),
            supplier.get("id"),
        ])


def upsert_certification(con: duckdb.DuckDBPyConnection, cert: dict) -> None:
    """Insert or update a certification record."""
    cert_id = f"{cert['supplier_id']}:{cert['source']}:{cert.get('license_id', 'unknown')}"
    con.execute("""
        INSERT INTO certifications (
            id, supplier_id, license_id, source, status, valid_until, certificate_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            status = excluded.status,
            valid_until = excluded.valid_until,
            verified_at = NOW()
    """, [
        cert_id,
        cert.get("supplier_id"),
        cert.get("license_id"),
        cert.get("source"),
        cert.get("status"),
        cert.get("valid_until"),
        cert.get("certificate_name"),
    ])
def upsert_trade_stat(con: duckdb.DuckDBPyConnection, stat: dict) -> None:
    """Insert or update a trade stat record."""
    stat_id = f"{stat['reporter_code']}:{stat['partner_code']}:{stat['year']}:{stat['hs_code']}"
    con.execute("""
        INSERT INTO trade_stats (
            id, reporter_code, partner_code, year, hs_code, trade_value_usd, net_weight_kg
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            trade_value_usd = excluded.trade_value_usd,
            net_weight_kg = excluded.net_weight_kg,
            updated_at = NOW()
    """, [
        stat_id,
        stat.get("reporter_code"),
        stat.get("partner_code"),
        stat.get("year"),
        stat.get("hs_code"),
        stat.get("trade_value_usd"),
        stat.get("net_weight_kg"),
    ])
