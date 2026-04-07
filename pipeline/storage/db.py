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
    if db_path != ":memory:":
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
            category            VARCHAR DEFAULT 'textile', -- 'textile' | 'chemical' | ...
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
            suggestion_count    INTEGER DEFAULT 0,        -- crowdsourced hits for auto-promotion
            is_verified         BOOLEAN DEFAULT FALSE,    -- manual/trusted match flag
            category            VARCHAR DEFAULT 'textile', -- mirrors suppliers.category
            resolved_at         TIMESTAMP DEFAULT NOW()
        );
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS entity_rejections (
            alias_normalized    VARCHAR NOT NULL,
            canonical_id        VARCHAR NOT NULL,
            reason_code         VARCHAR,              -- Optional: 'wrong_subsidiary', 'not_supplier'
            rejected_at         TIMESTAMP DEFAULT NOW(),
            PRIMARY KEY (alias_normalized, canonical_id)
        );
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS admin_audit_log (
            id                 VARCHAR PRIMARY KEY,   -- uuid4 hex
            action             VARCHAR NOT NULL,       -- 'verify' | 'reject'
            alias_ids          VARCHAR NOT NULL,       -- JSON array of acted-on IDs
            canonical_id       VARCHAR,               -- canonical_id of first alias
            reason_code        VARCHAR,
            snapshot_json      VARCHAR,               -- SNAPSHOT: captures state for 'undo'
            is_undone          BOOLEAN DEFAULT FALSE,
            undo_reason        VARCHAR,
            acted_at           TIMESTAMP DEFAULT NOW()
        );
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS tenants (
            id                 VARCHAR PRIMARY KEY,   -- uuid4 hex
            name               VARCHAR NOT NULL,
            tier               VARCHAR DEFAULT 'tier_1', -- 'tier_1', 'tier_2', 'enterprise'
            status             VARCHAR DEFAULT 'active', -- 'active', 'suspended'
            created_at         TIMESTAMP DEFAULT NOW()
        );
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            hashed_key         VARCHAR PRIMARY KEY,   -- sha256 of raw key
            tenant_id          VARCHAR REFERENCES tenants(id),
            prefix             VARCHAR NOT NULL,      -- first 8 chars for display
            is_active          BOOLEAN DEFAULT TRUE,
            created_at         TIMESTAMP DEFAULT NOW(),
            last_used_at       TIMESTAMP
        );
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS usage_logs (
            id                 VARCHAR PRIMARY KEY,   -- uuid4 hex
            tenant_id          VARCHAR REFERENCES tenants(id),
            endpoint           VARCHAR NOT NULL,      -- e.g. '/v1/score'
            method             VARCHAR NOT NULL,      -- e.g. 'POST'
            status_code        INTEGER,
            called_at          TIMESTAMP DEFAULT NOW()
        );
    """)

    # ---------------------------------------------------------------- #
    # Schema migrations — safe to run on existing databases            #
    # ---------------------------------------------------------------- #
    # DuckDB 1.5+ raises DependencyException on ALTER TABLE when any
    # index exists on the target table, even for a true no-op column
    # addition. Guard every migration with an explicit column-existence
    # check so we skip the ALTER entirely when already applied.
    def _has_column(table: str, column: str) -> bool:
        rows = con.execute(f"PRAGMA table_info('{table}')").fetchall()
        return any(r[1] == column for r in rows)

    if not _has_column("suppliers", "category"):
        con.execute("ALTER TABLE suppliers ADD COLUMN category VARCHAR DEFAULT 'textile'")
    if not _has_column("entity_aliases", "category"):
        con.execute("ALTER TABLE entity_aliases ADD COLUMN category VARCHAR DEFAULT 'textile'")
    if not _has_column("admin_audit_log", "snapshot_json"):
        con.execute("ALTER TABLE admin_audit_log ADD COLUMN snapshot_json VARCHAR")
    if not _has_column("admin_audit_log", "is_undone"):
        con.execute("ALTER TABLE admin_audit_log ADD COLUMN is_undone BOOLEAN DEFAULT FALSE")
    if not _has_column("admin_audit_log", "undo_reason"):
        con.execute("ALTER TABLE admin_audit_log ADD COLUMN undo_reason VARCHAR")

    # Indexes on hot query paths
    con.execute("CREATE INDEX IF NOT EXISTS idx_trust_score      ON trust_scores(trust_score)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_trust_supplier   ON trust_scores(supplier_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_supplier_country ON suppliers(country)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_cert_supplier    ON certifications(supplier_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_cert_status      ON certifications(status)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_alias_norm       ON entity_aliases(alias_normalized)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_alias_canonical  ON entity_aliases(canonical_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_alias_category   ON entity_aliases(category)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_supplier_category ON suppliers(category)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_audit_acted_at    ON admin_audit_log(acted_at DESC)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_usage_tenant      ON usage_logs(tenant_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_usage_called_at   ON usage_logs(called_at DESC)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_tenant   ON api_keys(tenant_id)")

    # ---------------------------------------------------------------- #
    # resolver_config — Laplace-smoothed rejection rate per canonical.  #
    # Used by EntityResolver to compute per-supplier adaptive threshold. #
    #                                                                    #
    # Formula: (rejections + 1) / (rejections + verifications + 2)      #
    #   - New supplier (0/0): 1/2 = 0.5  → neutral, no penalty          #
    #   - 10 rejections, 0 verified: 11/12 ≈ 0.92 → near-max penalty   #
    #   - 10 rejections, 10 verified: 11/22 = 0.5 → penalty reset       #
    # ---------------------------------------------------------------- #
    con.execute("""
        CREATE OR REPLACE VIEW resolver_config AS
        SELECT
            s.id                                          AS canonical_id,
            COALESCE(r.rejection_count,    0)             AS rejection_count,
            COALESCE(v.verification_count, 0)             AS verification_count,
            (COALESCE(r.rejection_count, 0) + 1.0) /
            (COALESCE(r.rejection_count, 0) +
             COALESCE(v.verification_count, 0) + 2.0)    AS laplace_rejection_rate
        FROM suppliers s
        LEFT JOIN (
            SELECT canonical_id, COUNT(*) AS rejection_count
            FROM   entity_rejections
            GROUP  BY canonical_id
        ) r ON r.canonical_id = s.id
        LEFT JOIN (
            SELECT canonical_id, COUNT(*) AS verification_count
            FROM   entity_aliases
            WHERE  is_verified = TRUE
            GROUP  BY canonical_id
        ) v ON v.canonical_id = s.id
    """)

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
                source, raw_url, category
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            supplier.get("category", "textile"),
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
