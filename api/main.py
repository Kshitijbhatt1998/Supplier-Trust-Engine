"""
Textile Supplier Trust Engine — FastAPI

Endpoints:
  POST /score         Score a supplier by name (looks up DuckDB, runs model)
  GET  /supplier/{id} Get full supplier profile with trust score
  GET  /suppliers     List all scored suppliers
  GET  /health        Healthcheck
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from loguru import logger
import json

from pipeline.storage.db import init_db
from model.features import engineer_features, MODEL_FEATURES
from model.scorer import score_supplier

app = FastAPI(
    title="Textile Supplier Trust Engine",
    description="DataVibe — Supplier fulfillment risk scoring for trade intelligence",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

con = init_db()


# ------------------------------------------------------------------ #
# Schemas                                                               #
# ------------------------------------------------------------------ #

class ScoreRequest(BaseModel):
    supplier_id: Optional[str] = None
    supplier_name: Optional[str] = None


class TrustScoreResponse(BaseModel):
    supplier_id: str
    supplier_name: str
    country: Optional[str]
    trust_score: float           # 0–100
    risk_probability: float      # 0–1
    risk_flags: list[str]        # SHAP-driven human-readable flags
    certification_status: dict   # oekotex, gots
    shipment_summary: dict       # count, frequency, buyers


# ------------------------------------------------------------------ #
# Endpoints                                                             #
# ------------------------------------------------------------------ #

@app.get("/health")
def health():
    return {"status": "ok", "service": "textile-trust-engine"}


@app.post("/score", response_model=TrustScoreResponse)
def score(req: ScoreRequest):
    """
    Score a supplier by ID or name.
    Pulls features from DuckDB and runs the LightGBM model.
    """
    if not req.supplier_id and not req.supplier_name:
        raise HTTPException(400, "Provide supplier_id or supplier_name")

    # Lookup supplier
    if req.supplier_id:
        row = con.execute(
            "SELECT * FROM suppliers WHERE id = ?", [req.supplier_id]
        ).fetchone()
    else:
        row = con.execute(
            "SELECT * FROM suppliers WHERE lower(name) LIKE lower(?)",
            [f"%{req.supplier_name}%"]
        ).fetchone()

    if not row:
        raise HTTPException(404, f"Supplier not found: {req.supplier_id or req.supplier_name}")

    cols = [desc[0] for desc in con.description]
    supplier = dict(zip(cols, row))

    # Pull engineered features
    features_df = engineer_features(con)
    feat_row = features_df[features_df["id"] == supplier["id"]]

    if feat_row.empty:
        raise HTTPException(500, "Could not engineer features for this supplier")

    features = feat_row.iloc[0].to_dict()

    # Score
    try:
        result = score_supplier(features)
    except FileNotFoundError:
        raise HTTPException(503, "Model not trained yet. Run: python model/scorer.py --train")

    # Pull certification data
    certs = con.execute(
        "SELECT source, status, valid_until FROM certifications WHERE supplier_id = ?",
        [supplier["id"]]
    ).fetchall()

    cert_status = {
        c[0]: {"status": c[1], "valid_until": str(c[2]) if c[2] else None}
        for c in certs
    }

    return TrustScoreResponse(
        supplier_id=supplier["id"],
        supplier_name=supplier["name"],
        country=supplier.get("country"),
        trust_score=result["trust_score"],
        risk_probability=result["risk_probability"],
        risk_flags=result["risk_flags"],
        certification_status=cert_status,
        shipment_summary={
            "total_shipments": supplier.get("shipment_count"),
            "avg_monthly": supplier.get("avg_monthly_shipments"),
            "total_buyers": supplier.get("total_buyers"),
            "last_shipment": str(supplier.get("last_shipment_date")) if supplier.get("last_shipment_date") else None,
        }
    )


@app.get("/suppliers")
def list_suppliers(min_score: float = 0, country: Optional[str] = None, limit: int = 50):
    """List all scored suppliers, optionally filtered."""
    query = """
        SELECT s.id, s.name, s.country, t.trust_score, t.shap_flags_json
        FROM suppliers s
        JOIN trust_scores t ON t.supplier_id = s.id
        WHERE t.trust_score >= ?
    """
    params = [min_score]

    if country:
        query += " AND s.country ILIKE ?"
        params.append(f"%{country}%")

    query += f" ORDER BY t.trust_score DESC LIMIT {limit}"
    rows = con.execute(query, params).fetchall()

    return [
        {
            "id": r[0],
            "name": r[1],
            "country": r[2],
            "trust_score": r[3],
            "top_risk_flags": json.loads(r[4]) if r[4] else [],
        }
        for r in rows
    ]


@app.get("/supplier/{supplier_id}")
def get_supplier(supplier_id: str):
    """Full profile for a single supplier."""
    return score(ScoreRequest(supplier_id=supplier_id))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
