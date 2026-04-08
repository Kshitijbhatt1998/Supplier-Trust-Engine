import lightgbm as lgb
import pandas as pd
import numpy as np
import pickle
import shap
from pathlib import Path
from loguru import logger
from sklearn.model_selection import train_test_split
from model.features_chemical import engineer_chemical_features, CHEM_MODEL_FEATURES

def generate_synthetic_chem_data(n_samples=500):
    """
    Generate synthetic chemical training data.
    Heuristic: 
    - High CAS linkage + Reg Hub = High Trust
    - Low Frequency + Low Buyers = Low Trust/Broker Signal
    """
    rs = np.random.RandomState(42)
    
    data = {
        "cas_linkage_score": rs.uniform(0.3, 1.0, n_samples),
        "grade_purity_index": rs.uniform(0.3, 1.0, n_samples),
        "frequency_stability": rs.uniform(0.1, 1.0, n_samples),
        "regulatory_hub_score": rs.uniform(0.5, 1.0, n_samples),
        "buyer_network_diversity": rs.uniform(0.1, 1.0, n_samples),
        "shipment_count": rs.randint(1, 2000, n_samples)
    }
    
    df = pd.DataFrame(data)
    
    # Calculate latent trust score (0-100)
    # Weights: CAS(25), Purity(15), Regulatory(20), Frequency(20), Diversity(10), Volume(10)
    noise = rs.normal(0, 3, n_samples)
    target = (
        df["cas_linkage_score"] * 25 +
        df["grade_purity_index"] * 15 +
        df["regulatory_hub_score"] * 30 +
        df["frequency_stability"] * 20 +
        df["buyer_network_diversity"] * 5 +
        (df["shipment_count"] / 2000) * 5 +
        noise
    )
    
    df["trust_score"] = target.clip(0, 100)
    return df

def train_chemical_model():
    logger.info("Initializing Chemical Model Training...")
    
    # 1. Prepare Data
    df = generate_synthetic_chem_data(1000)
    X = df[CHEM_MODEL_FEATURES]
    y = df["trust_score"]
    
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    
    # 2. Train LightGBM
    params = {
        "objective": "regression",
        "metric": "rmse",
        "verbosity": -1,
        "boosting_type": "gbdt",
        "learning_rate": 0.05,
        "num_leaves": 31,
        "feature_fraction": 0.8
    }
    
    train_data = lgb.Dataset(X_train, label=y_train)
    test_data = lgb.Dataset(X_test, label=y_test, reference=train_data)
    
    model = lgb.train(
        params,
        train_data,
        num_boost_round=200,
        valid_sets=[train_data, test_data],
        callbacks=[lgb.early_stopping(stopping_rounds=20)]
    )
    
    # 3. Save Model
    model_path = Path("model/chemical_trust_model.pkl")
    with open(model_path, "wb") as f:
        pickle.dump(model, f)
    
    # 4. Generate SHAP Explainer
    explainer = shap.TreeExplainer(model)
    with open("model/chemical_shap_explainer.pkl", "wb") as f:
        pickle.dump(explainer, f)
        
    logger.success(f"Chemical Trust Model saved to {model_path}")
    
if __name__ == "__main__":
    train_chemical_model()
