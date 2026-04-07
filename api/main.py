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
import uuid
from enum import Enum
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
from api.auth import get_api_key, get_admin_key
from api.resolver import EntityResolver
from api.chemical_normalizer import _ROLE_NOISE as _CHEM_ROLE_NOISE


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
    allow_methods=["GET", "POST", "OPTIONS"],  # OPTIONS required for preflight
    allow_headers=["Content-Type", "X-API-Key", "X-Admin-Token"],
    allow_credentials=False,  # tokens are in headers, not cookies
)


# ------------------------------------------------------------------ #
# Security headers middleware                                          #
# ------------------------------------------------------------------ #
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # Only set HSTS when running over TLS (nginx handles TLS termination in prod)
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


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


class FeedbackRequest(BaseModel):
    supplier_name: str = Field(..., max_length=200)
    canonical_id:  str = Field(..., max_length=100)
    is_confirmed:  bool = True
    reason_code:   Optional[str] = None


class SupplierCategory(str, Enum):
    textile  = "textile"
    chemical = "chemical"


class AdminActionRequest(BaseModel):
    alias_ids:   list[str] = Field(..., max_length=200)
    action:      str # 'verify' or 'reject'
    reason_code: Optional[str] = None


class AdminUndoRequest(BaseModel):
    audit_id:    str
    undo_reason: str = Field(..., min_length=1, max_length=200)


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
    resolution_metadata:  Optional[dict] = None


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

    res_metadata = None

    if req.supplier_id:
        row = con.execute(
            "SELECT * FROM suppliers WHERE id = ?", [req.supplier_id]
        ).fetchone()
    else:
        # Use EntityResolver for fuzzy name lookups
        resolver = EntityResolver(con)
        res = resolver.resolve(req.supplier_name)
        supplier_id = res.get('supplier_id')
        
        if not supplier_id:
            raise HTTPException(404, f"Supplier not found: {req.supplier_name} (Best match score: {res.get('match_score', 0):.1f})")
            
        if not res.get('is_verified'):
            # Fetch preview data for the candidate to help the user verify
            cand_id = res.get('supplier_id')
            cand_profile = con.execute("""
                SELECT trust_score, shap_flags_json 
                FROM trust_scores WHERE supplier_id = ?
            """, [cand_id]).fetchone()
            
            res_metadata = {
                'match_type': res.get('match_type'),
                'match_score': res.get('match_score'),
                'canonical_name': res.get('canonical_name'),
                'is_subsidiary_warning': res.get('is_subsidiary_warning'),
                'low_confidence': res.get('low_confidence', False),
                'preview_score': cand_profile[0] if cand_profile else 0,
                'preview_flags': json.loads(cand_profile[1]) if cand_profile and cand_profile[1] else []
            }

        row = con.execute(
            "SELECT * FROM suppliers WHERE id = ?", [supplier_id]
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
        resolution_metadata=res_metadata
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
@limiter.limit("5/minute")
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


@v1.post("/resolver/feedback")
@limiter.limit("20/minute")
def resolver_feedback(req: FeedbackRequest, request: Request, key: str = Depends(get_api_key)):
    """
    User feedback loop. 
    If confirmed=True: Increment suggestion_count and potentially promote alias.
    If confirmed=False: Add to rejections list to prevent future suggestions.
    """
    resolver = EntityResolver(con)
    normalized = resolver.normalize(req.supplier_name)
    
    if req.is_confirmed:
        # Positive feedback
        con.execute("""
            UPDATE entity_aliases 
            SET suggestion_count = suggestion_count + 1,
                resolved_at = NOW()
            WHERE alias_normalized = ? AND canonical_id = ?
        """, [normalized, req.canonical_id])
        
        # Auto-promotion logic (e.g. 3 confirmations)
        con.execute("""
            UPDATE entity_aliases
            SET is_verified = TRUE
            WHERE alias_normalized = ? AND canonical_id = ? AND suggestion_count >= 3
        """, [normalized, req.canonical_id])
    else:
        # Negative feedback: Add to rejections (using ON CONFLICT to ensure idempotency)
        con.execute("""
            INSERT INTO entity_rejections (alias_normalized, canonical_id, reason_code)
            VALUES (?, ?, ?)
            ON CONFLICT (alias_normalized, canonical_id) DO NOTHING
        """, [normalized, req.canonical_id, req.reason_code or 'user_rejected'])
    
    return {"status": "success", "message": "Feedback recorded"}


# ------------------------------------------------------------------ #
# Admin Dashboard Endpoints                                          #
# ------------------------------------------------------------------ #

@v1.get("/admin/review-queue")
@limiter.limit("10/minute")
def admin_review_queue(
    request:  Request,
    key:      str                       = Depends(get_admin_key),
    category: Optional[SupplierCategory] = Query(None),
):
    """
    Returns a prioritized queue of unverified aliases for human audit.
    Priority P = (0.4 * cap(V, 100)/100) + (0.3 * T/100) + (0.3 * S/100)
    """
    # Constants must match EntityResolver class attributes
    BASE_THRESHOLD   = 85.0
    PENALTY_WEIGHT   = 12.0
    MAX_THRESHOLD    = 97.0

    query = """
        SELECT
            a.id,
            a.alias_name,
            a.alias_normalized,
            a.canonical_id,
            a.match_score,
            a.suggestion_count,
            s.name                                                  AS canonical_name,
            t.trust_score,
            t.shap_flags_json,
            -- Priority Score P (capped-volume normalisation)
            (0.4 * LEAST(a.suggestion_count, 100) / 100.0) +
            (0.3 * COALESCE(t.trust_score, 0)     / 100.0) +
            (0.3 * a.match_score                  / 100.0)         AS priority_score,
            -- Adaptive threshold components from resolver_config view
            COALESCE(rc.rejection_count,    0)                      AS rejection_count,
            COALESCE(rc.verification_count, 0)                      AS verification_count,
            COALESCE(rc.laplace_rejection_rate, 0.5)                AS laplace_rejection_rate
        FROM entity_aliases a
        JOIN  suppliers     s  ON s.id              = a.canonical_id
        LEFT JOIN trust_scores  t  ON t.supplier_id = a.canonical_id
        LEFT JOIN resolver_config rc ON rc.canonical_id = a.canonical_id
        WHERE a.is_verified = FALSE
    """
    params: list = []
    if category:
        query += " AND a.category = ?"
        params.append(category)
    query += " ORDER BY priority_score DESC LIMIT 100"
    rows = con.execute(query, params).fetchall()

    def _threshold(rate: float) -> float:
        return min(BASE_THRESHOLD + rate * PENALTY_WEIGHT, MAX_THRESHOLD)

    def _cas_from_id(canonical_id: str) -> Optional[str]:
        """Extract bare CAS number from a cas-NNNNNN-NN-N canonical ID, else None."""
        if canonical_id.startswith("cas-"):
            return canonical_id[4:]  # strip "cas-" prefix
        return None

    return [
        {
            "id":                 r[0],
            "alias_name":         r[1],
            "alias_normalized":   r[2],
            "canonical_id":       r[3],
            "match_score":        r[4],
            "suggestion_count":   r[5],
            "canonical_name":     r[6],
            "trust_score":        r[7],
            "shap_flags":         json.loads(r[8]) if r[8] else [],
            "priority_score":     round(r[9], 4),
            "rejection_count":    r[10],
            "verification_count": r[11],
            "adaptive_threshold": round(_threshold(float(r[12])), 1),
            "cas_number":         _cas_from_id(r[3]),
            "is_role_warning":    bool(_CHEM_ROLE_NOISE.search(r[1] or "")),
        }
        for r in rows
    ]

@v1.post("/admin/alias/action")
@limiter.limit("10/minute")
def admin_alias_action(req: AdminActionRequest, request: Request, key: str = Depends(get_admin_key)):
    """
    Bulk verify or reject aliases.
    """
    if not req.alias_ids:
        return {"status": "ignored", "message": "No IDs provided"}

    if req.action not in ('verify', 'reject'):
        raise HTTPException(400, f"Invalid action '{req.action}': must be 'verify' or 'reject'")

    # Capture snapshots for restoration / audit (Snapshot Version 1)
    alias_rows = con.execute("""
        SELECT id, alias_name, alias_normalized, canonical_id, match_score, suggestion_count, category
        FROM entity_aliases WHERE id IN (SELECT UNNEST(?))
    """, [req.alias_ids]).fetchall()

    if not alias_rows:
         raise HTTPException(404, "None of the targeting aliases were found")

    snapshot = {
        "version": 1,
        "data": [
            {
                "id": r[0], "name": r[1], "normalized": r[2], "canonical_id": r[3],
                "score": r[4], "count": r[5], "category": r[6]
            } for r in alias_rows
        ]
    }

    con.execute("""
        INSERT INTO admin_audit_log (id, action, alias_ids, canonical_id, reason_code, snapshot_json)
        VALUES (?, ?, ?, ?, ?, ?)
    """, [
        uuid.uuid4().hex,
        req.action,
        json.dumps(req.alias_ids),
        alias_rows[0][3], # Use first one for grouping
        req.reason_code,
        json.dumps(snapshot)
    ])

    if req.action == 'verify':
        # Single click promotion
        for aid in req.alias_ids:
            con.execute("UPDATE entity_aliases SET is_verified = TRUE WHERE id = ?", [aid])
            
    elif req.action == 'reject':
        # Move to negative cache and delete from aliases
        for aid in req.alias_ids:
            # Get data first
            row = con.execute("SELECT alias_normalized, canonical_id FROM entity_aliases WHERE id = ?", [aid]).fetchone()
            if row:
                con.execute("""
                    INSERT INTO entity_rejections (alias_normalized, canonical_id, reason_code)
                    VALUES (?, ?, ?)
                    ON CONFLICT DO NOTHING
                """, [row[0], row[1], req.reason_code or 'admin_rejected'])
                
                con.execute("DELETE FROM entity_aliases WHERE id = ?", [aid])
                
    return {"status": "success", "count": len(req.alias_ids)}


@v1.get("/admin/audit-logs")
@limiter.limit("20/minute")
def admin_audit_logs(
    request: Request,
    key:      str                       = Depends(get_admin_key),
    category: Optional[SupplierCategory] = Query(None),
):
    """Returns recent administrative actions."""
    query = """
        SELECT
            l.id, l.action, l.alias_ids, l.canonical_id, s.name AS canonical_name,
            l.reason_code, l.acted_at, l.is_undone, l.undo_reason
        FROM admin_audit_log l
        LEFT JOIN suppliers s ON s.id = l.canonical_id
        WHERE 1=1
    """
    params = []
    if category:
        query += " AND s.category = ?"
        params.append(category)

    query += " ORDER BY l.acted_at DESC LIMIT 50"
    rows = con.execute(query, params).fetchall()

    return [
        {
            "id": r[0], "action": r[1], "alias_ids": json.loads(r[2]),
            "canonical_id": r[3], "canonical_name": r[4], "reason_code": r[5],
            "acted_at": r[6].isoformat(), "is_undone": bool(r[7]), "undo_reason": r[8]
        } for r in rows
    ]


@v1.post("/admin/audit/undo")
@limiter.limit("5/minute")
def admin_undo(req: AdminUndoRequest, request: Request, key: str = Depends(get_admin_key)):
    """Atomic reversal of a previous admin action (24h window)."""
    log = con.execute("""
        SELECT action, alias_ids, snapshot_json, is_undone, acted_at
        FROM admin_audit_log WHERE id = ?
    """, [req.audit_id]).fetchone()

    if not log:
        raise HTTPException(404, "Audit entry not found")
    if log[3]: # is_undone
        raise HTTPException(400, "Action already undone")

    # 24-hour safety window check
    import datetime
    if log[4] < datetime.datetime.now() - datetime.timedelta(days=1):
        raise HTTPException(400, "Undo window (24h) has expired")

    action = log[0]
    alias_ids = json.loads(log[1])
    snapshot = json.loads(log[2])

    # Validate snapshot schema before using it for restoration
    if not isinstance(snapshot, dict) or snapshot.get("version") != 1:
        raise HTTPException(400, "Snapshot format is invalid or unsupported")
    snap_items = snapshot.get("data", [])
    _REQUIRED_SNAP_KEYS = {"id", "name", "normalized", "canonical_id", "score", "count", "category"}
    for item in snap_items:
        if not isinstance(item, dict) or not _REQUIRED_SNAP_KEYS.issubset(item.keys()):
            raise HTTPException(400, "Snapshot data is malformed; cannot safely undo")

    try:
        con.execute("BEGIN TRANSACTION")

        if action == 'verify':
            # Reverse verify: set to false and PENALISE the suggestion count
            con.execute("""
                UPDATE entity_aliases
                SET is_verified = FALSE,
                    suggestion_count = GREATEST(suggestion_count - 1, 0)
                WHERE id IN (SELECT UNNEST(?))
            """, [alias_ids])

        elif action == 'reject':
            # Restore from snapshot and purge from rejections
            for item in snapshot["data"]:
                 con.execute("""
                    INSERT INTO entity_aliases (id, alias_name, alias_normalized, canonical_id, match_score, suggestion_count, is_verified, category)
                    VALUES (?, ?, ?, ?, ?, ?, FALSE, ?)
                    ON CONFLICT(id) DO UPDATE SET is_verified = FALSE
                 """, [item['id'], item['name'], item['normalized'], item['canonical_id'], item['score'], item['count'], item['category']])

                 con.execute("""
                    DELETE FROM entity_rejections
                    WHERE alias_normalized = ? AND canonical_id = ?
                 """, [item['normalized'], item['canonical_id']])

        # Mark log as undone
        con.execute("""
            UPDATE admin_audit_log
            SET is_undone = TRUE, undo_reason = ?
            WHERE id = ?
        """, [req.undo_reason, req.audit_id])

        con.execute("COMMIT")
        return {"status": "success", "message": f"Action {action} reversed"}

    except Exception as e:
        con.execute("ROLLBACK")
        logger.error(f"Undo failed for audit_id={req.audit_id}: {e}")
        raise HTTPException(500, "Undo operation failed. Check server logs.")


app.include_router(v1)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
