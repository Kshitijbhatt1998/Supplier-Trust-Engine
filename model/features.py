"""
Feature Engineering Pipeline

Transforms raw scraped data in DuckDB into model-ready features.
This is the bridge between the scraper and the LightGBM model.

Features engineered:
- years_active: proxy for business maturity
- customer_concentration_ratio: are they captive to one buyer? (risk signal)
- hs_code_diversity: manufacturer vs. middleman signal
- shipment_frequency_score: consistency of operations
- certification_score: weighted cert validity
- volume_claimed_vs_actual: bait-and-switch risk (when data allows)
"""

import duckdb
import pandas as pd
import numpy as np
from datetime import datetime
from loguru import logger
from typing import Optional

from pipeline.storage.db import init_db


def engineer_features(con: Optional[duckdb.DuckDBPyConnection] = None) -> pd.DataFrame:
    """
    Pull raw supplier + certification data and compute all features.
    Returns a DataFrame ready for model training or scoring.
    """
    if con is None:
        con = init_db()

    # --- Base supplier data ---
    suppliers = con.execute("""
        SELECT
            s.id,
            s.name,
            s.country,
            s.shipment_count,
            s.avg_monthly_shipments,
            s.total_buyers,
            s.hs_codes,
            s.top_buyers,
            s.first_shipment_date,
            s.last_shipment_date,
            s.scraped_at,
            -- Certification counts
            COUNT(DISTINCT c.id) FILTER (WHERE c.status = 'valid')   AS valid_cert_count,
            COUNT(DISTINCT c.id) FILTER (WHERE c.status = 'expired') AS expired_cert_count,
            COUNT(DISTINCT c.id) FILTER (WHERE c.source = 'oekotex' AND c.status = 'valid') AS oekotex_valid,
            COUNT(DISTINCT c.id) FILTER (WHERE c.source = 'gots'    AND c.status = 'valid') AS gots_valid,
            -- Manifest verification count
            COUNT(DISTINCT sh.id) AS verified_shipment_count
        FROM suppliers s
        LEFT JOIN certifications c ON c.supplier_id = s.id
        LEFT JOIN shipments sh ON sh.supplier_id = s.id
        GROUP BY s.id, s.name, s.country, s.shipment_count,
                 s.avg_monthly_shipments, s.total_buyers,
                 s.hs_codes, s.top_buyers,
                 s.first_shipment_date, s.last_shipment_date, s.scraped_at
    """).df()

    # --- National Trade Stats (Comtrade) ---
    trade_stats = con.execute("""
        SELECT reporter_code, hs_code, MAX(trade_value_usd) as nat_volume_usd
        FROM trade_stats
        GROUP BY reporter_code, hs_code
    """).df()

    if suppliers.empty:
        logger.warning("No suppliers found in DB — run the scraper first.")
        return pd.DataFrame()

    logger.info(f"Engineering features for {len(suppliers)} suppliers...")

    # ------------------------------------------------------------------ #
    # Feature 1: years_active                                              #
    # ------------------------------------------------------------------ #
    today = pd.Timestamp.now()
    suppliers["first_shipment_date"] = pd.to_datetime(suppliers["first_shipment_date"], errors="coerce")
    suppliers["last_shipment_date"] = pd.to_datetime(suppliers["last_shipment_date"], errors="coerce")

    suppliers["years_active"] = (
        (today - suppliers["first_shipment_date"]).dt.days / 365.25
    ).clip(lower=0)

    # ------------------------------------------------------------------ #
    # Feature 2: days_since_last_shipment (recency)                        #
    # ------------------------------------------------------------------ #
    suppliers["days_since_last_shipment"] = (
        (today - suppliers["last_shipment_date"]).dt.days
    ).clip(lower=0)

    # ------------------------------------------------------------------ #
    # Feature 3: customer_concentration_ratio                              #
    # High = captive factory or middleman. Low = diverse, healthy.         #
    # Ratio = 1 / max(total_buyers, 1) ... inverted so high = risky       #
    # ------------------------------------------------------------------ #
    suppliers["total_buyers"] = suppliers["total_buyers"].fillna(0).astype(int)
    suppliers["customer_concentration_ratio"] = 1 / (suppliers["total_buyers"].clip(lower=1))

    # ------------------------------------------------------------------ #
    # Feature 4: hs_code_diversity                                         #
    # Manufacturers have focused HS codes. Middlemen spread wide.          #
    # ------------------------------------------------------------------ #
    suppliers["hs_code_count"] = suppliers["hs_codes"].apply(
        lambda x: len(x) if isinstance(x, list) else 0
    )
    # Number of unique 2-digit HS chapters (broad product diversity = middleman signal)
    suppliers["hs_chapter_diversity"] = suppliers["hs_codes"].apply(
        lambda x: len(set(str(c)[:2] for c in x)) if isinstance(x, list) else 0
    )

    # ------------------------------------------------------------------ #
    # Feature 5: shipment_frequency_score                                  #
    # avg_monthly_shipments normalized by years_active                     #
    # ------------------------------------------------------------------ #
    suppliers["avg_monthly_shipments"] = suppliers["avg_monthly_shipments"].fillna(0)
    suppliers["shipment_frequency_score"] = (
        suppliers["avg_monthly_shipments"] /
        (suppliers["years_active"].clip(lower=0.1))
    ).clip(upper=50)

    # ------------------------------------------------------------------ #
    # Feature 6: certification_score (0–3)                                 #
    # GOTS = 2 pts (hardest to fake), OEKO-TEX = 1 pt                     #
    # ------------------------------------------------------------------ #
    suppliers["certification_score"] = (
        suppliers["gots_valid"] * 2 +
        suppliers["oekotex_valid"] * 1
    ).clip(upper=3)

    suppliers["has_any_valid_cert"] = (suppliers["valid_cert_count"] > 0).astype(int)
    suppliers["has_expired_cert"] = (suppliers["expired_cert_count"] > 0).astype(int)

    # ------------------------------------------------------------------ #
    # Feature 7: is_high_volume_shipper                                    #
    # Proxy for being a real manufacturer vs. a broker                     #
    # ------------------------------------------------------------------ #
    suppliers["shipment_count"] = suppliers["shipment_count"].fillna(0).astype(int)
    suppliers["is_high_volume_shipper"] = (
        suppliers["shipment_count"] > suppliers["shipment_count"].median()
    ).astype(int)

    # ------------------------------------------------------------------ #
    # Feature 8: country_risk_score                                        #
    # Simple lookup — extend as needed                                      #
    # ------------------------------------------------------------------ #
    country_risk = {
        # Lower score = lower risk
        "China": 0.4,
        "India": 0.3,
        "Bangladesh": 0.5,
        "Vietnam": 0.3,
        "Turkey": 0.2,
        "Portugal": 0.1,
        "Italy": 0.1,
        "Pakistan": 0.5,
        "Indonesia": 0.3,
        "Cambodia": 0.5,
    }
    suppliers["country_risk_score"] = (
        suppliers["country"].map(country_risk).fillna(0.35)
    )

    # ------------------------------------------------------------------ #
    # Feature 9: manifest_verification_score                               #
    # % of claimed shipments found in public manifest records              #
    # ------------------------------------------------------------------ #
    suppliers["manifest_verification_score"] = (
        suppliers["verified_shipment_count"] / 
        (suppliers["shipment_count"].clip(lower=1))
    ).clip(upper=1.0)

    # ------------------------------------------------------------------ #
    # Feature 10: national_volume_share                                     #
    # Mapping country name to M49 for joining with Comtrade stats          #
    # ------------------------------------------------------------------ #
    m49_map = {
        "Bangladesh": "050", "China": "156", "India": "356",
        "Italy": "380", "Pakistan": "586", "Portugal": "620",
        "Turkey": "792", "Vietnam": "704",
    }
    suppliers["m49_code"] = suppliers["country"].map(m49_map)
    
    # Calculate a simplified national market share score
    # We take the first HS code as major product and compare with national export value
    def get_market_share(row):
        if not row["m49_code"] or not isinstance(row["hs_codes"], list) or not row["hs_codes"]:
            return 0.0
        major_hs = str(row["hs_codes"][0])[:4]
        match = trade_stats[
            (trade_stats["reporter_code"] == row["m49_code"]) & 
            (trade_stats["hs_code"] == major_hs)
        ]
        if not match.empty:
            nat_vol = match.iloc[0]["nat_volume_usd"]
            # Estimate supplier volume: shipments * estimated $5k avg value
            est_vol = row["shipment_count"] * 5000 
            return (est_vol / nat_vol) if nat_vol > 0 else 0.0
        return 0.0

    suppliers["national_market_share"] = suppliers.apply(get_market_share, axis=1)

    # ------------------------------------------------------------------ #
    # Select final feature columns                                          #
    # ------------------------------------------------------------------ #
    feature_cols = [
        "id", "name", "country",
        # Numeric features for the model
        "years_active",
        "days_since_last_shipment",
        "customer_concentration_ratio",
        "hs_code_count",
        "hs_chapter_diversity",
        "shipment_frequency_score",
        "certification_score",
        "has_any_valid_cert",
        "has_expired_cert",
        "is_high_volume_shipper",
        "country_risk_score",
        "manifest_verification_score",
        "national_market_share",
        "shipment_count",
        "avg_monthly_shipments",
        "total_buyers",
        "valid_cert_count",
    ]

    result = suppliers[feature_cols].copy()
    logger.info(f"Feature engineering complete. Shape: {result.shape}")
    return result


MODEL_FEATURES = [
    "years_active",
    "days_since_last_shipment",
    "customer_concentration_ratio",
    "hs_code_count",
    "hs_chapter_diversity",
    "shipment_frequency_score",
    "certification_score",
    "has_any_valid_cert",
    "has_expired_cert",
    "is_high_volume_shipper",
    "country_risk_score",
    "manifest_verification_score",
    "national_market_share",
    "shipment_count",
    "avg_monthly_shipments",
    "total_buyers",
    "valid_cert_count",
]


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    df = engineer_features()
    print(df.head())
    print(df.describe())
