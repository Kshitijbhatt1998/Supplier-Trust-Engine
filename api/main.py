"""
Textile Supplier Trust Engine — FastAPI

Endpoints:
  POST /score               Score a supplier by name or ID
  POST /procure/evaluate    AI Decision Engine — autonomous procurement filtering
  GET  /supplier/{id}       Full supplier profile with trust score
  GET  /suppliers           List all scored suppliers (filterable)
  GET  /health              Healthcheck
"""

from pipeline.storage.db import init_db
from model.features import engineer_features, MODEL_FEATURES
from model.scorer import score_supplier
from api.decision_engine import DecisionEngine, ProcurementCriteria
from api.auth import get_api_key

from fastapi import Depends, Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from contextlib import asynccontextmanager

# Rate limiting setup
limiter = Limiter(key_func=get_remote_address)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB connection on startup
    global con
    con = init_db()
    yield
    # Cleanup on shutdown
    con.close()

app = FastAPI(
    title="Textile Supplier Trust Engine",
    description="DataVibe — Supplier fulfillment risk scoring for trade intelligence. "
                "Powers autonomous AI procurement agents.",
    version="0.2.0",
    lifespan=lifespan
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global connection (initialized in lifespan)
con = None


# ------------------------------------------------------------------ #
# Schemas                                                               #
# ------------------------------------------------------------------ #

class ScoreRequest(BaseModel):
    supplier_id: Optional[str] = None
    supplier_name: Optional[str] = None


class TrustScoreResponse(BaseModel):
    supplier_id: str
    supplier_name: str
    country: Optional[str] = None
    trust_score: float            # 0–100
    risk_probability: float       # 0–1
    risk_flags: list[str]         # SHAP-driven human-readable flags
    certification_status: dict    # oekotex, gots
    shipment_summary: dict        # count, frequency, buyers
    trade_proof: dict             # manifest_score, market_share


class ProcureRequest(BaseModel):
    category: str
    min_trust_score: float = 75.0
    required_certs: list[str] = []
    country_prefer: list[str] = []
    country_exclude: list[str] = []
    max_days_inactive: int = 365
    max_results: int = 5


# ------------------------------------------------------------------ #
# Endpoints                                                             #
# ------------------------------------------------------------------ #

@app.get("/health")
def health():
    n = con.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]
    return {"status": "ok", "service": "textile-trust-engine", "suppliers_in_db": n}


@app.post("/score", response_model=TrustScoreResponse)
@limiter.limit("10/minute")
def score(req: ScoreRequest, request: Request, key: str = Depends(get_api_key)):
    """
    Score a supplier by ID or name.
    Pulls features from DuckDB and runs the LightGBM model.
    """
    if not req.supplier_id and not req.supplier_name:
        raise HTTPException(400, "Provide supplier_id or supplier_name")

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

    # Feature engineering
    features_df = engineer_features(con)
    feat_row = features_df[features_df["id"] == supplier["id"]]

    if feat_row.empty:
        raise HTTPException(500, "Could not engineer features for this supplier")

    features = feat_row.iloc[0].to_dict()

    # Score
    try:
        result = score_supplier(features)
    except FileNotFoundError:
        raise HTTPException(
            503,
            "Model not trained yet. Run: python run_pipeline.py --seed --train --score"
        )

    # Certification data
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
            "total_shipments":  supplier.get("shipment_count"),
            "avg_monthly":      supplier.get("avg_monthly_shipments"),
            "total_buyers":     supplier.get("total_buyers"),
            "last_shipment":    str(supplier.get("last_shipment_date")) if supplier.get("last_shipment_date") else None,
        },
        trade_proof={
            "manifest_verification_score": features.get("manifest_verification_score", 0),
            "national_market_share":       features.get("national_market_share", 0),
        }
    )


@app.post("/procure/evaluate")
@limiter.limit("5/minute")
def procure_evaluate(req: ProcureRequest, request: Request, key: str = Depends(get_api_key)):
    """
    AI Procurement Decision Engine.

    An AI micro-business sends procurement criteria; this endpoint queries
    the trust database, applies hard filters, ranks results, and returns
    a list of approved suppliers with rationale.

    Example — AI agent looking for GOTS-certified Indian suppliers:
        {
          "category": "organic cotton tote bags",
          "min_trust_score": 80,
          "required_certs": ["gots"],
          "country_prefer": ["India", "Turkey"],
          "country_exclude": [],
          "max_days_inactive": 180,
          "max_results": 3
        }
    """
    criteria = ProcurementCriteria(
        category=req.category,
        min_trust_score=req.min_trust_score,
        required_certs=req.required_certs,
        country_prefer=req.country_prefer,
        country_exclude=req.country_exclude,
        max_days_inactive=req.max_days_inactive,
        max_results=req.max_results,
    )

    engine = DecisionEngine(con)
    decision = engine.evaluate(criteria)

    # Serialize the dataclass to a plain dict
    return {
        "approved": decision.approved,
        "category": decision.category,
        "criteria_used": decision.criteria_used,
        "decision_rationale": decision.decision_rationale,
        "fallback_message": decision.fallback_message,
        "matched_suppliers": [
            {
                "supplier_id":            m.supplier_id,
                "supplier_name":          m.supplier_name,
                "country":                m.country,
                "trust_score":            m.trust_score,
                "rank_score":             round(m.rank_score, 2),
                "risk_flags":             m.risk_flags,
                "certification_status":   m.certification_status,
                "shipment_count":         m.shipment_count,
                "days_since_last_shipment": m.days_since_last_shipment,
                "match_reasons":          m.match_reasons,
            }
            for m in decision.matched_suppliers
        ],
    }


@app.get("/suppliers")
def list_suppliers(
    request: Request,
    min_score: float = 0, 
    country: Optional[str] = None, 
    limit: int = 50,
    key: str = Depends(get_api_key)
):
    """List all scored suppliers, optionally filtered by min trust score or country."""
    query = """
        SELECT s.id, s.name, s.country, t.trust_score, t.shap_flags_json
        FROM suppliers s
        JOIN trust_scores t ON t.supplier_id = s.id
        WHERE t.trust_score >= ?
    """
    params: list = [min_score]

    if country:
        query += " AND s.country ILIKE ?"
        params.append(f"%{country}%")

    query += f" ORDER BY t.trust_score DESC LIMIT {limit}"
    rows = con.execute(query, params).fetchall()

    return [
        {
            "id":             r[0],
            "name":           r[1],
            "country":        r[2],
            "trust_score":    r[3],
            "top_risk_flags": json.loads(r[4]) if r[4] else [],
        }
        for r in rows
    ]


@app.get("/supplier/{supplier_id}", response_model=TrustScoreResponse)
def get_supplier(supplier_id: str, request: Request, key: str = Depends(get_api_key)):
    """Full trust profile for a single supplier."""
    return score(ScoreRequest(supplier_id=supplier_id), request, key)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
