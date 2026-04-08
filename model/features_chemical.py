import duckdb
import pandas as pd
import numpy as np
from loguru import logger
from typing import Optional
from pipeline.storage.db import init_db

def engineer_chemical_features(con: Optional[duckdb.DuckDBPyConnection] = None) -> pd.DataFrame:
    """
    Computes chemical-specific features for the Trust Engine.
    Excludes textile-specific certification checks (GOTS/Oeko-Tex).
    """
    if con is None:
        con = init_db()

    # Pull chemical suppliers
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
            s.address,
            -- Chemical specific signals from product descriptors (mocked from address/name for now if no product table)
            s.name || ' ' || COALESCE(s.address, '') as raw_text
        FROM suppliers s
        WHERE s.category = 'chemical'
    """).df()

    if suppliers.empty:
        logger.warning("No chemical suppliers found to engineer features for.")
        return pd.DataFrame()

    logger.info(f"Engineering chemical features for {len(suppliers)} entities...")

    # 1. CAS Match Ratio (Mocked for now - in production we'd count CAS matches in manifest)
    # Higher CAS density in shipment logs = higher manufacturer trust
    suppliers["cas_linkage_score"] = suppliers["hs_codes"].apply(
        lambda x: 0.8 if any(str(c).startswith(('28', '29', '38')) for c in x) else 0.4
    )

    # 2. Grade Analysis (Technical, USP, Food, Reagent)
    def extract_grade_score(text):
        text = text.lower()
        if any(w in text for w in ['reagent', 'usp', 'laboratory']): return 1.0
        if any(w in text for w in ['technical', 'industrial']): return 0.6
        return 0.4
    
    suppliers["grade_purity_index"] = suppliers["raw_text"].apply(extract_grade_score)

    # 3. Shipment Frequency (Borrowed from textile, but higher weight for chemicals)
    suppliers["avg_monthly_shipments"] = suppliers["avg_monthly_shipments"].fillna(0)
    suppliers["frequency_stability"] = (suppliers["avg_monthly_shipments"] / 10).clip(upper=1.0)

    # 4. Regulatory Proximity (Mocked: Entities in certain hubs get higher baseline inspection scores)
    suppliers["regulatory_hub_score"] = suppliers["country"].apply(
        lambda x: 0.9 if x in ['USA', 'Germany', 'Japan', 'Switzerland'] else 0.6
    )

    # 5. Customer Diversification
    suppliers["total_buyers"] = suppliers["total_buyers"].fillna(0).clip(lower=1)
    suppliers["buyer_network_diversity"] = (suppliers["total_buyers"] / 20).clip(upper=1.0)

    # Final feature selection
    feature_cols = [
        "id", "name",
        "cas_linkage_score",
        "grade_purity_index",
        "frequency_stability",
        "regulatory_hub_score",
        "buyer_network_diversity",
        "shipment_count"
    ]

    return suppliers[feature_cols]

# Constants for the trainer
CHEM_MODEL_FEATURES = [
    "cas_linkage_score",
    "grade_purity_index",
    "frequency_stability",
    "regulatory_hub_score",
    "buyer_network_diversity",
    "shipment_count"
]
