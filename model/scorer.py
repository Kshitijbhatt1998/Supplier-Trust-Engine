"""
Textile Supplier Trust Scoring Model

LightGBM classifier: predicts fulfillment risk (0 = reliable, 1 = risky).
SHAP values drive the "risk flags" shown in the API response —
these are what make the score interpretable, not just a black box number.

Training flow:
1. engineer_features() pulls from DuckDB
2. You label a subset manually (see notebooks/label_suppliers.ipynb)
3. Train model, inspect SHAP, iterate on features
4. Save model artifact
5. API loads model and scores new suppliers on-demand
"""

import os
import json
import pickle
import numpy as np
import pandas as pd
import lightgbm as lgb
import shap
from loguru import logger
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score, classification_report
from typing import Optional

from model.features import engineer_features, MODEL_FEATURES as TEXTILE_FEATURES
from model.features_chemical import engineer_chemical_features, CHEM_MODEL_FEATURES as CHEMICAL_FEATURES
from pipeline.storage.db import init_db

# Model Paths
TEXTILE_MODEL_PATH = "model/trust_model.pkl"
TEXTILE_SHAP_PATH  = "model/shap_explainer.pkl"

CHEMICAL_MODEL_PATH = "model/chemical_trust_model.pkl"
CHEMICAL_SHAP_PATH  = "model/chemical_shap_explainer.pkl"

# Human-readable flag names for SHAP output
FEATURE_LABELS = {
    # Common
    "years_active":                 "Short operating history",
    "days_since_last_shipment":     "Inactive recently (no recent shipments)",
    "customer_concentration_ratio": "High customer concentration (captive factory risk)",
    "shipment_count":               "Low total shipment count",
    "avg_monthly_shipments":        "Low average monthly shipments",
    "total_buyers":                 "Very few distinct buyers",
    "manifest_verification_score":  "Unverified shipment claims (no matching manifest records)",
    
    # Textile Specific
    "certification_score":          "Missing or weak certifications",
    "has_any_valid_cert":           "No valid certifications found",
    "has_expired_cert":             "Has expired certifications (lapsed compliance)",
    "is_high_volume_shipper":       "Low shipment volume vs. industry peers",
    "country_risk_score":           "Higher-risk manufacturing country",
    
    # Chemical Specific
    "cas_linkage_score":            "Low CAS/Registry linkage in trade data",
    "grade_purity_index":           "Low purity or technical-only grade focus",
    "frequency_stability":          "Inconsistent shipment frequency for chemicals",
    "regulatory_hub_score":         "Non-primary chemical regulatory jurisdiction",
    "buyer_network_diversity":      "Fragile buyer network",
}


# ------------------------------------------------------------------ #
# Training                                                              #
# ------------------------------------------------------------------ #

# ... (train function remains similar but would need category branching if called)

# ------------------------------------------------------------------ #
# Scoring                                                               #
# ------------------------------------------------------------------ #

def load_model(category: str = "textile"):
    path = CHEMICAL_MODEL_PATH if category == "chemical" else TEXTILE_MODEL_PATH
    if not os.path.exists(path):
        logger.warning(f"Model for {category} not found at {path}. Falling back to default.")
        path = TEXTILE_MODEL_PATH
    with open(path, "rb") as f:
        return pickle.load(f)


def load_explainer(category: str = "textile"):
    path = CHEMICAL_SHAP_PATH if category == "chemical" else TEXTILE_SHAP_PATH
    if not os.path.exists(path):
        logger.warning(f"Explainer for {category} not found at {path}. Falling back to default.")
        path = TEXTILE_SHAP_PATH
    with open(path, "rb") as f:
        return pickle.load(f)


def score_supplier(features: dict, category: str = "textile") -> dict:
    """
    Score a single supplier dict and return:
    - trust_score (0–100, higher = more trustworthy)
    - risk_probability (raw model output)
    - risk_flags (top SHAP-driven reasons for risk)
    """
    model = load_model(category)
    explainer = load_explainer(category)
    
    feature_list = CHEMICAL_FEATURES if category == "chemical" else TEXTILE_FEATURES

    X = pd.DataFrame([{f: features.get(f, 0) for f in feature_list}])
    X = X.fillna(0)

    # Some models are Classifiers (predict_proba), some are Regressors (predict)
    # The Chemical model I just trained is a Regressor.
    if hasattr(model, "predict_proba"):
        risk_prob = model.predict_proba(X)[0][1]  # P(risky)
    else:
        # For chemical regressor, predicted value is the trust score (0-100)
        trust_pred = model.predict(X)[0]
        risk_prob = 1 - (trust_pred / 100)

    trust_score = round((1 - risk_prob) * 100, 1)

    # SHAP interpretation
    shap_values = explainer.shap_values(X)
    if isinstance(shap_values, list):
        # Classifier output
        shap_vals = shap_values[1][0] if len(shap_values) > 1 else shap_values[0]
    else:
        # Regressor output
        shap_vals = shap_values[0]

    # Build risk flag list
    shap_pairs = list(zip(feature_list, shap_vals))
    
    # For regressor, negative SHAP values are "risk drivers" (lowering the trust score)
    # For classifier, positive SHAP values are "risk drivers" (increasing risk prob)
    if hasattr(model, "predict_proba"):
        shap_pairs.sort(key=lambda x: x[1], reverse=True)
        risk_flags = [FEATURE_LABELS.get(feat, feat) for feat, val in shap_pairs[:3] if val > 0]
    else:
        shap_pairs.sort(key=lambda x: x[1]) # Most negative first
        risk_flags = [FEATURE_LABELS.get(feat, feat) for feat, val in shap_pairs[:3] if val < 0]

    return {
        "trust_score": trust_score,
        "risk_probability": round(float(risk_prob), 4),
        "risk_flags": risk_flags,
        "feature_snapshot": {f: round(float(X[f].iloc[0]), 3) for f in feature_list},
        "category": category
    }


def score_all_and_store() -> None:
    """Score all suppliers in DuckDB and write trust scores back."""
    con = init_db()
    
    # 1. Score Textiles
    textile_df = engineer_features(con)
    if not textile_df.empty:
        logger.info(f"Scoring {len(textile_df)} textile suppliers...")
        _process_df(con, textile_df, "textile")

    # 2. Score Chemicals
    chemical_df = engineer_chemical_features(con)
    if not chemical_df.empty:
        logger.info(f"Scoring {len(chemical_df)} chemical suppliers...")
        _process_df(con, chemical_df, "chemical")

    logger.info("Scoring complete.")


import uuid

def _process_df(con, df, category):
    for _, row in df.iterrows():
        try:
            # Fetch existing score for comparison
            prev = con.execute("SELECT trust_score FROM trust_scores WHERE supplier_id = ?", [row["id"]]).fetchone()
            old_score = prev[0] if prev else None

            result = score_supplier(row.to_dict(), category)
            new_score = result["trust_score"]

            con.execute("""
                INSERT INTO trust_scores (supplier_id, trust_score, risk_label, feature_json, shap_flags_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (supplier_id) DO UPDATE SET
                    trust_score = excluded.trust_score,
                    risk_label  = excluded.risk_label,
                    feature_json = excluded.feature_json,
                    shap_flags_json = excluded.shap_flags_json,
                    scored_at = NOW()
            """, [
                row["id"],
                new_score,
                1 if result["risk_probability"] > 0.5 else 0,
                json.dumps(result["feature_snapshot"]),
                json.dumps(result["risk_flags"]),
            ])

            # Log history if changed
            if old_score is not None and abs(new_score - old_score) >= 1.0:
                con.execute("""
                    INSERT INTO supplier_score_history (id, supplier_id, old_score, new_score, risk_label, reason_code)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, [
                    uuid.uuid4().hex,
                    row["id"],
                    old_score,
                    new_score,
                    1 if result["risk_probability"] > 0.5 else 0,
                    "re-score_batch"
                ])
                
                # Check for "Score Drop" trigger for webhooks
                if new_score < old_score - 5.0:
                    logger.warning(f"  🔔 ALERT: Significant score drop for {row['name']} ({old_score} -> {new_score})")
                    # TODO: Trigger webhook_worker.deliver_alerts()

            logger.success(f"  [{category.upper()}] {row['name']}: {new_score}/100")
        except Exception as e:
            logger.warning(f"  Failed to score {row['name']}: {e}")

    logger.info("Scoring complete.")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    score_all_and_store()
