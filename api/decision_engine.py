"""
AI Decision Engine for Autonomous Procurement

This is the "Synthetic CEO" layer. An AI micro-business sends a ProcurementRequest
describing what it needs; this engine queries the trust database, applies business
rules, and returns a ranked, explainable procurement decision.

Flow:
  AI agent ──► POST /procure/evaluate ──► DecisionEngine ──► DuckDB trust scores
                                               │
                                               ▼
                                      ProcurementDecision
                                      (approved suppliers + rationale)

Filter logic (all filters are ANDed):
  1. min_trust_score    — hard floor (default 75)
  2. required_certs     — must have at least one valid cert of each type listed
  3. country_exclude    — skip suppliers from high-risk or sanctioned countries
  4. country_prefer     — boost rank for preferred manufacturing regions
  5. max_days_inactive  — skip suppliers with no recent shipment activity
"""

import json
from typing import Optional
from dataclasses import dataclass, field
from loguru import logger

import duckdb


# ------------------------------------------------------------------ #
# Request / Response models (plain dataclasses — Pydantic in api/main)  #
# ------------------------------------------------------------------ #

@dataclass
class ProcurementCriteria:
    """What the AI agent needs."""
    category: str                            # e.g. "organic cotton tote bags"
    min_trust_score: float = 75.0            # hard floor
    required_certs: list[str] = field(default_factory=list)   # ["gots", "oekotex"]
    country_prefer: list[str] = field(default_factory=list)   # ["India", "Turkey"]
    country_exclude: list[str] = field(default_factory=list)  # ["North Korea"]
    max_days_inactive: int = 365             # skip suppliers with no shipments in N days
    max_results: int = 5


@dataclass
class SupplierMatch:
    """A single supplier that passed all filters."""
    supplier_id: str
    supplier_name: str
    country: Optional[str]
    trust_score: float
    risk_flags: list[str]
    certification_status: dict       # {"gots": "valid", "oekotex": "expired"}
    shipment_count: int
    days_since_last_shipment: int
    rank_score: float                # composite ranking score (trust + preference boost)
    match_reasons: list[str]         # human-readable why it was selected


@dataclass
class ProcurementDecision:
    """The engine's output — directly consumable by an AI agent."""
    approved: bool
    category: str
    criteria_used: dict
    matched_suppliers: list[SupplierMatch]
    decision_rationale: str
    fallback_message: Optional[str] = None   # set if approved=False


# ------------------------------------------------------------------ #
# Core engine                                                           #
# ------------------------------------------------------------------ #

class DecisionEngine:
    """
    Queries DuckDB trust scores and applies procurement criteria to return
    a ranked, filtered list of suppliers an AI agent can act on immediately.
    """

    CERT_WEIGHT = 5.0          # trust score bonus per valid required cert
    COUNTRY_PREF_BOOST = 8.0   # rank boost for preferred countries

    def __init__(self, con: duckdb.DuckDBPyConnection):
        self.con = con

    def evaluate(self, criteria: ProcurementCriteria) -> ProcurementDecision:
        """
        Main entry point. Runs all filter + ranking steps and returns a decision.
        """
        logger.info(
            f"DecisionEngine evaluating procurement: '{criteria.category}' "
            f"(min_score={criteria.min_trust_score}, certs={criteria.required_certs})"
        )

        # Step 1: Pull candidates above trust floor
        candidates = self._fetch_candidates(criteria)

        if not candidates:
            return ProcurementDecision(
                approved=False,
                category=criteria.category,
                criteria_used=self._criteria_to_dict(criteria),
                matched_suppliers=[],
                decision_rationale=(
                    f"No suppliers found with trust score ≥ {criteria.min_trust_score}. "
                    "Consider lowering the threshold or expanding the country preference."
                ),
                fallback_message="Widen criteria or wait for more supplier data to be scraped.",
            )

        # Step 2: Apply hard filters (certs, exclusions, recency)
        filtered = self._apply_filters(candidates, criteria)

        if not filtered:
            return ProcurementDecision(
                approved=False,
                category=criteria.category,
                criteria_used=self._criteria_to_dict(criteria),
                matched_suppliers=[],
                decision_rationale=(
                    f"Found {len(candidates)} suppliers above trust floor but none passed "
                    f"all hard filters (certs={criteria.required_certs}, "
                    f"exclude={criteria.country_exclude}, "
                    f"active_within={criteria.max_days_inactive}d)."
                ),
                fallback_message="Try relaxing required_certs or max_days_inactive.",
            )

        # Step 3: Rank by composite score
        ranked = self._rank(filtered, criteria)

        top = ranked[: criteria.max_results]

        return ProcurementDecision(
            approved=True,
            category=criteria.category,
            criteria_used=self._criteria_to_dict(criteria),
            matched_suppliers=top,
            decision_rationale=self._build_rationale(top, criteria),
        )

    # ------------------------------------------------------------------ #
    # Step 1: Fetch candidates above trust floor                            #
    # ------------------------------------------------------------------ #

    def _fetch_candidates(self, criteria: ProcurementCriteria) -> list[dict]:
        """Pull suppliers + trust scores from DuckDB above the trust floor."""
        query = """
            SELECT
                s.id,
                s.name,
                s.country,
                s.shipment_count,
                s.last_shipment_date,
                t.trust_score,
                t.shap_flags_json
            FROM suppliers s
            JOIN trust_scores t ON t.supplier_id = s.id
            WHERE t.trust_score >= ?
            ORDER BY t.trust_score DESC
        """
        rows = self.con.execute(query, [criteria.min_trust_score]).fetchall()
        cols = ["id", "name", "country", "shipment_count", "last_shipment_date",
                "trust_score", "shap_flags_json"]
        return [dict(zip(cols, r)) for r in rows]

    # ------------------------------------------------------------------ #
    # Step 2: Hard filters                                                  #
    # ------------------------------------------------------------------ #

    def _apply_filters(
        self, candidates: list[dict], criteria: ProcurementCriteria
    ) -> list[dict]:
        filtered = []
        for row in candidates:
            # Country exclusion
            if criteria.country_exclude and row["country"] in criteria.country_exclude:
                logger.debug(f"  Excluded {row['name']} — country {row['country']}")
                continue

            # Recency: skip inactive suppliers
            days_inactive = self._days_since(row["last_shipment_date"])
            if days_inactive > criteria.max_days_inactive:
                logger.debug(
                    f"  Excluded {row['name']} — inactive {days_inactive}d "
                    f"(max {criteria.max_days_inactive}d)"
                )
                continue

            # Certification requirements
            if criteria.required_certs:
                cert_status = self._get_cert_status(row["id"])
                missing = [
                    c for c in criteria.required_certs
                    if cert_status.get(c) != "valid"
                ]
                if missing:
                    logger.debug(
                        f"  Excluded {row['name']} — missing certs: {missing}"
                    )
                    continue
                row["_cert_status"] = cert_status
            else:
                row["_cert_status"] = self._get_cert_status(row["id"])

            row["_days_inactive"] = days_inactive
            filtered.append(row)

        logger.info(
            f"  Filtered: {len(candidates)} candidates → {len(filtered)} passed filters"
        )
        return filtered

    # ------------------------------------------------------------------ #
    # Step 3: Rank                                                          #
    # ------------------------------------------------------------------ #

    def _rank(
        self, filtered: list[dict], criteria: ProcurementCriteria
    ) -> list[SupplierMatch]:
        matches = []
        for row in filtered:
            rank_score = float(row["trust_score"])
            match_reasons: list[str] = [
                f"Trust score {row['trust_score']:.1f}/100"
            ]

            # Country preference boost
            if criteria.country_prefer and row["country"] in criteria.country_prefer:
                rank_score += self.COUNTRY_PREF_BOOST
                match_reasons.append(f"Preferred country ({row['country']})")

            # Cert bonus (already required by filter, so this rewards extras)
            cert_status = row.get("_cert_status", {})
            valid_certs = [k for k, v in cert_status.items() if v == "valid"]
            if valid_certs:
                rank_score += len(valid_certs) * self.CERT_WEIGHT
                match_reasons.append(f"Valid certs: {', '.join(valid_certs).upper()}")

            # Parse SHAP flags
            try:
                risk_flags = json.loads(row["shap_flags_json"]) if row["shap_flags_json"] else []
            except (json.JSONDecodeError, TypeError):
                risk_flags = []

            matches.append(SupplierMatch(
                supplier_id=row["id"],
                supplier_name=row["name"],
                country=row["country"],
                trust_score=row["trust_score"],
                risk_flags=risk_flags,
                certification_status=cert_status,
                shipment_count=row["shipment_count"] or 0,
                days_since_last_shipment=row.get("_days_inactive", 0),
                rank_score=rank_score,
                match_reasons=match_reasons,
            ))

        matches.sort(key=lambda m: m.rank_score, reverse=True)
        return matches

    # ------------------------------------------------------------------ #
    # Helpers                                                               #
    # ------------------------------------------------------------------ #

    def _get_cert_status(self, supplier_id: str) -> dict:
        """Fetch current cert status for a supplier from DuckDB."""
        rows = self.con.execute(
            "SELECT source, status FROM certifications WHERE supplier_id = ?",
            [supplier_id],
        ).fetchall()
        # If multiple certs per source, prefer 'valid' over others
        status: dict[str, str] = {}
        for source, s in rows:
            if status.get(source) != "valid":
                status[source] = s
        return status

    def _days_since(self, last_date) -> int:
        """Calculate days between last_date and today. Returns large int if null."""
        if last_date is None:
            return 9999
        from datetime import date, datetime
        if isinstance(last_date, str):
            try:
                last_date = datetime.strptime(last_date, "%Y-%m-%d").date()
            except ValueError:
                return 9999
        if isinstance(last_date, datetime):
            last_date = last_date.date()
        return (date.today() - last_date).days

    def _criteria_to_dict(self, criteria: ProcurementCriteria) -> dict:
        return {
            "category": criteria.category,
            "min_trust_score": criteria.min_trust_score,
            "required_certs": criteria.required_certs,
            "country_prefer": criteria.country_prefer,
            "country_exclude": criteria.country_exclude,
            "max_days_inactive": criteria.max_days_inactive,
            "max_results": criteria.max_results,
        }

    def _build_rationale(
        self, suppliers: list[SupplierMatch], criteria: ProcurementCriteria
    ) -> str:
        if not suppliers:
            return "No approved suppliers."
        top = suppliers[0]
        lines = [
            f"Approved {len(suppliers)} supplier(s) for '{criteria.category}'.",
            f"Top recommendation: {top.supplier_name} ({top.country}) — "
            f"trust score {top.trust_score}/100.",
        ]
        if top.risk_flags:
            lines.append(f"Watch flags: {'; '.join(top.risk_flags)}.")
        if len(suppliers) > 1:
            others = ", ".join(s.supplier_name for s in suppliers[1:])
            lines.append(f"Alternates: {others}.")
        return " ".join(lines)
