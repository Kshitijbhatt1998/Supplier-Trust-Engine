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

from model.features import engineer_features, MODEL_FEATURES
from pipeline.storage.db import init_db

MODEL_PATH = "model/trust_model.pkl"
SHAP_EXPLAINER_PATH = "model/shap_explainer.pkl"

# Human-readable flag names for SHAP output
FEATURE_LABELS = {
    "years_active":                 "Short operating history",
    "days_since_last_shipment":     "Inactive recently (no recent shipments)",
    "customer_concentration_ratio": "High customer concentration (captive factory risk)",
    "hs_code_count":                "Too few HS codes (limited product range)",
    "hs_chapter_diversity":         "Extremely broad product spread (middleman signal)",
    "shipment_frequency_score":     "Low shipment frequency",
    "certification_score":          "Missing or weak certifications",
    "has_any_valid_cert":           "No valid certifications found",
    "has_expired_cert":             "Has expired certifications (lapsed compliance)",
    "is_high_volume_shipper":       "Low shipment volume vs. industry peers",
    "country_risk_score":           "Higher-risk manufacturing country",
    "shipment_count":               "Low total shipment count",
    "avg_monthly_shipments":        "Low average monthly shipments",
    "total_buyers":                 "Very few distinct buyers",
    "valid_cert_count":             "No valid certifications",
    "manifest_verification_score":  "Unverified shipment claims (no matching manifest records)",
    "national_market_share":        "Volume discrepancy vs. national trade statistics",
}


# ------------------------------------------------------------------ #
# Training                                                              #
# ------------------------------------------------------------------ #

def train(labeled_csv: str = "data/labeled_suppliers.csv") -> None:
    """
    Train the trust scoring model.

    Expects a CSV with columns:
    - supplier_id (str)
    - risk_label  (int): 0 = reliable, 1 = risky/middleman

    Generate this via notebooks/label_suppliers.ipynb
    """
    if not os.path.exists(labeled_csv):
        logger.error(
            f"No labeled data at {labeled_csv}. "
            "Run notebooks/label_suppliers.ipynb first to manually label ~30-50 suppliers."
        )
        return

    labels = pd.read_csv(labeled_csv)[["id", "risk_label"]]
    features_df = engineer_features()

    df = features_df.merge(labels, on="id", how="inner")
    if len(df) < 20:
        logger.warning(f"Only {len(df)} labeled samples — model will be weak. Label more suppliers.")

    X = df[MODEL_FEATURES].fillna(0)
    y = df["risk_label"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y if y.nunique() > 1 else None
    )

    model = lgb.LGBMClassifier(
        n_estimators=200,
        learning_rate=0.05,
        max_depth=4,
        num_leaves=15,
        min_child_samples=5,      # Small value needed for small datasets
        class_weight="balanced",
        random_state=42,
        verbose=-1,
    )
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)])

    y_prob = model.predict_proba(X_test)[:, 1]
    auc = roc_auc_score(y_test, y_prob)
    logger.info(f"Test AUC: {auc:.4f}")
    logger.info("\n" + classification_report(y_test, model.predict(X_test)))

    # Save model
    os.makedirs("model", exist_ok=True)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump(model, f)

    # Build and save SHAP explainer
    explainer = shap.TreeExplainer(model)
    with open(SHAP_EXPLAINER_PATH, "wb") as f:
        pickle.dump(explainer, f)

    logger.success(f"Model saved to {MODEL_PATH}")


# ------------------------------------------------------------------ #
# Scoring                                                               #
# ------------------------------------------------------------------ #

def load_model():
    with open(MODEL_PATH, "rb") as f:
        return pickle.load(f)


def load_explainer():
    with open(SHAP_EXPLAINER_PATH, "rb") as f:
        return pickle.load(f)


def score_supplier(features: dict) -> dict:
    """
    Score a single supplier dict and return:
    - trust_score (0–100, higher = more trustworthy)
    - risk_probability (raw model output)
    - risk_flags (top SHAP-driven reasons for risk)

    Called by the FastAPI endpoint.
    """
    model = load_model()
    explainer = load_explainer()

    X = pd.DataFrame([{f: features.get(f, 0) for f in MODEL_FEATURES}])
    X = X.fillna(0)

    risk_prob = model.predict_proba(X)[0][1]  # P(risky)
    trust_score = round((1 - risk_prob) * 100, 1)

    # SHAP: find which features are driving risk UP
    shap_values = explainer.shap_values(X)
    if isinstance(shap_values, list):
        shap_vals = shap_values[1][0]  # Class 1 (risky) SHAP values
    else:
        shap_vals = shap_values[0]

    # Build risk flag list: features with positive SHAP (pushing toward risky)
    shap_pairs = list(zip(MODEL_FEATURES, shap_vals))
    shap_pairs.sort(key=lambda x: x[1], reverse=True)

    risk_flags = [
        FEATURE_LABELS.get(feat, feat)
        for feat, val in shap_pairs[:3]   # Top 3 risk drivers
        if val > 0
    ]

    return {
        "trust_score": trust_score,
        "risk_probability": round(float(risk_prob), 4),
        "risk_flags": risk_flags,
        "feature_snapshot": {f: round(float(X[f].iloc[0]), 3) for f in MODEL_FEATURES},
    }


def score_all_and_store() -> None:
    """Score all suppliers in DuckDB and write trust scores back."""
    con = init_db()
    features_df = engineer_features(con)

    if features_df.empty:
        logger.warning("No features to score.")
        return

    logger.info(f"Scoring {len(features_df)} suppliers...")

    for _, row in features_df.iterrows():
        try:
            result = score_supplier(row.to_dict())
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
                result["trust_score"],
                1 if result["risk_probability"] > 0.5 else 0,
                json.dumps(result["feature_snapshot"]),
                json.dumps(result["risk_flags"]),
            ])
            logger.success(f"  {row['name']}: {result['trust_score']}/100 — {result['risk_flags']}")
        except Exception as e:
            logger.warning(f"  Failed to score {row['name']}: {e}")

    logger.info("Scoring complete.")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    import sys
    if "--train" in sys.argv:
        train()
    else:
        score_all_and_store()
