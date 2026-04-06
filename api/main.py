"""
Textile Supplier Trust Engine — FastAPI v1

All routes versioned under /v1/.

Auth model:
  - Dashboard GET endpoints (health, stats, suppliers, supplier/{id}) → no key required.
    These are served by your own nginx proxy — not exposed directly to the internet.
  - AI agent POST endpoints (score, procure/evaluate) → X-API-Key required.
    External callers must present a valid key.
"""

import os
import json
from typing import Optional
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI, HTTPException, Depends, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRouter
from pydantic import BaseModel, Field, field_validator
from loguru import logger

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from pipeline.storage.db import init_db
from model.features import engineer_features, MODEL_FEATURES
from model.scorer import score_supplier
from api.decision_engine import DecisionEngine, ProcurementCriteria
from api.auth import get_api_key


# ------------------------------------------------------------------ #
# Sentry — only initialised when SENTRY_DSN is set in env             #
# ------------------------------------------------------------------ #
_sentry_dsn = os.getenv("SENTRY_DSN")
if _sentry_dsn:
    sentry_sdk.init(dsn=_sentry_dsn, traces_sample_rate=0.1)
    logger.info("Sentry error tracking initialised.")


# ------------------------------------------------------------------ #
# Rate limiter                                                          #
# ------------------------------------------------------------------ #
limiter = Limiter(key_func=get_remote_address)


# ------------------------------------------------------------------ #
# App lifespan                                                          #
# ------------------------------------------------------------------ #
con = None  # DuckDB connection, initialised in lifespan


@asynccontextmanager
async def lifespan(app: FastAPI):
    global con
    con = init_db()
    yield
    con.close()


# ------------------------------------------------------------------ #
# App                                                                   #
# ------------------------------------------------------------------ #
app = FastAPI(
    title="Textile Supplier Trust Engine",
    description=(
        "DataVibe — Supplier fulfillment risk scoring for trade intelligence. "
        "Powers autonomous AI procurement agents."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ------------------------------------------------------------------ #
# CORS — lock to configured origins (never *)                          #
# ------------------------------------------------------------------ #
_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost,http://localhost:80")
ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)


# ------------------------------------------------------------------ #
# Global error handler — never expose stack traces                     #
# ------------------------------------------------------------------ #
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error [{request.method} {request.url}]: {exc!r}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


# ------------------------------------------------------------------ #
# Schemas                                                               #
# ------------------------------------------------------------------ #

class ScoreRequest(BaseModel):
    supplier_id:   Optional[str] = Field(None, max_length=100)
    supplier_name: Optional[str] = Field(None, max_length=200)


class TrustScoreResponse(BaseModel):
    supplier_id:          str
    supplier_name:        str
    country:              Optional[str] = None
    trust_score:          float
    risk_probability:     float
    risk_flags:           list[str]
    certification_status: dict
    shipment_summary:     dict
    trade_proof:          dict


class ProcureRequest(BaseModel):
    category:          str   = Field(..., min_length=1, max_length=200)
    min_trust_score:   float = Field(75.0, ge=0.0, le=100.0)
    required_certs:    list[str] = Field(default_factory=list, max_length=10)
    country_prefer:    list[str] = Field(default_factory=list, max_length=20)
    country_exclude:   list[str] = Field(default_factory=list, max_length=20)
    max_days_inactive: int   = Field(365, ge=1, le=3650)
    max_results:       int   = Field(5, ge=1, le=20)

    @field_validator("required_certs", "country_prefer", "country_exclude", mode="before")
    @classmethod
    def clamp_string_lengths(cls, v):
        return [str(item)[:100] for item in v]


# ------------------------------------------------------------------ #
# Internal scoring helper (no auth dependency)                         #
# ------------------------------------------------------------------ #

def _score_supplier_by_request(req: ScoreRequest) -> TrustScoreResponse:
    if not req.supplier_id and not req.supplier_name:
        raise HTTPException(400, "Provide supplier_id or supplier_name")

    if req.supplier_id:
        row = con.execute(
            "SELECT * FROM suppliers WHERE id = ?", [req.supplier_id]
        ).fetchone()
    else:
        row = con.execute(
            "SELECT * FROM suppliers WHERE lower(name) LIKE lower(?)",
            [f"%{req.supplier_name}%"],
        ).fetchone()

    if not row:
        raise HTTPException(404, f"Supplier not found: {req.supplier_id or req.supplier_name}")

    cols     = [desc[0] for desc in con.description]
    supplier = dict(zip(cols, row))

    features_df = engineer_features(con)
    feat_row    = features_df[features_df["id"] == supplier["id"]]

    if feat_row.empty:
        raise HTTPException(500, "Could not engineer features for this supplier")

    features = feat_row.iloc[0].to_dict()

    try:
        result = score_supplier(features)
    except FileNotFoundError:
        raise HTTPException(
            503,
            "Model not trained yet. Run: python run_pipeline.py --seed --train --score",
        )

    certs = con.execute(
        "SELECT source, status, valid_until FROM certifications WHERE supplier_id = ?",
        [supplier["id"]],
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
            "avg_monthly":     supplier.get("avg_monthly_shipments"),
            "total_buyers":    supplier.get("total_buyers"),
            "last_shipment":   str(supplier.get("last_shipment_date")) if supplier.get("last_shipment_date") else None,
        },
        trade_proof={
            "manifest_verification_score": features.get("manifest_verification_score", 0),
            "national_market_share":       features.get("national_market_share", 0),
        },
    )


# ------------------------------------------------------------------ #
# v1 Router                                                             #
# ------------------------------------------------------------------ #
v1 = APIRouter(prefix="/v1")


# ── Public / dashboard-facing GET endpoints ──────────────────────── #

@v1.get("/health")
@limiter.limit("60/minute")
def health(request: Request):
    n = con.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]
    return {"status": "ok", "service": "textile-trust-engine", "suppliers_in_db": n}


@v1.get("/stats")
@limiter.limit("60/minute")
def stats(request: Request):
    """Aggregate counts for the dashboard stat cards."""
    total       = con.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]
    avg_score   = con.execute("SELECT AVG(trust_score) FROM trust_scores").fetchone()[0] or 0
    valid_certs = con.execute("SELECT COUNT(*) FROM certifications WHERE status = 'valid'").fetchone()[0]
    risk_alerts = con.execute("SELECT COUNT(*) FROM trust_scores WHERE trust_score < 40").fetchone()[0]
    return {
        "total_suppliers":  total,
        "avg_trust_score":  round(float(avg_score), 1),
        "valid_cert_count": valid_certs,
        "risk_alerts":      risk_alerts,
    }


@v1.get("/suppliers")
@limiter.limit("30/minute")
def list_suppliers(
    request: Request,
    min_score: float = Query(0, ge=0, le=100),
    country:   Optional[str] = Query(None, max_length=100),
    limit:     int   = Query(50, ge=1, le=200),
):
    """List all scored suppliers, optionally filtered by min trust score or country."""
    query  = """
        SELECT s.id, s.name, s.country, t.trust_score, t.shap_flags_json
        FROM suppliers s
        JOIN trust_scores t ON t.supplier_id = s.id
        WHERE t.trust_score >= ?
    """
    params: list = [min_score]

    if country:
        query += " AND s.country ILIKE ?"
        params.append(f"%{country}%")

    query += " ORDER BY t.trust_score DESC LIMIT ?"
    params.append(limit)

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


@v1.get("/supplier/{supplier_id}", response_model=TrustScoreResponse)
@limiter.limit("30/minute")
def get_supplier(
    supplier_id: str,
    request: Request,
):
    """Full trust profile for a single supplier."""
    return _score_supplier_by_request(ScoreRequest(supplier_id=supplier_id[:100]))


# ── Protected POST endpoints (X-API-Key required) ────────────────── #

@v1.post("/score", response_model=TrustScoreResponse)
@limiter.limit("10/minute")
def score(req: ScoreRequest, request: Request, key: str = Depends(get_api_key)):
    """Score a supplier by ID or name. Requires X-API-Key header."""
    return _score_supplier_by_request(req)


@v1.post("/procure/evaluate")
@limiter.limit("5/minute")
def procure_evaluate(req: ProcureRequest, request: Request, key: str = Depends(get_api_key)):
    """
    AI Procurement Decision Engine.

    An AI micro-business sends procurement criteria; this endpoint queries
    the trust database, applies hard filters, ranks results, and returns
    a list of approved suppliers with rationale.

    Example:
        {
          "category": "organic cotton tote bags",
          "min_trust_score": 80,
          "required_certs": ["gots"],
          "country_prefer": ["India", "Turkey"],
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
    engine   = DecisionEngine(con)
    decision = engine.evaluate(criteria)

    return {
        "approved":           decision.approved,
        "category":           decision.category,
        "criteria_used":      decision.criteria_used,
        "decision_rationale": decision.decision_rationale,
        "fallback_message":   decision.fallback_message,
        "matched_suppliers": [
            {
                "supplier_id":              m.supplier_id,
                "supplier_name":            m.supplier_name,
                "country":                  m.country,
                "trust_score":              m.trust_score,
                "rank_score":               round(m.rank_score, 2),
                "risk_flags":               m.risk_flags,
                "certification_status":     m.certification_status,
                "shipment_count":           m.shipment_count,
                "days_since_last_shipment": m.days_since_last_shipment,
                "match_reasons":            m.match_reasons,
            }
            for m in decision.matched_suppliers
        ],
    }


app.include_router(v1)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
