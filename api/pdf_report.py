"""
SourceGuard — PDF Due Diligence Report Generator

Produces a compliance-ready single-page PDF for a scored supplier.
Uses fpdf2 (pure Python, no external binaries required).
"""

import io
import json
from datetime import datetime
from fpdf import FPDF


RISK_LEVELS = [
    (80, "LOW RISK",      (74, 222, 128)),   # green
    (60, "MODERATE RISK", (251, 191, 36)),   # amber
    (40, "ELEVATED RISK", (251, 146, 60)),   # orange
    (0,  "HIGH RISK",     (248, 113, 113)),  # red
]


def _risk_level(score: float):
    for threshold, label, color in RISK_LEVELS:
        if score >= threshold:
            return label, color
    return "HIGH RISK", (248, 113, 113)


def generate_report(supplier: dict, score_data: dict, certs: list) -> bytes:
    """
    Build and return a PDF report as raw bytes.

    Parameters
    ----------
    supplier   : row from suppliers table as dict
    score_data : result from scorer.score_supplier()
    certs      : list of (source, status, valid_until) tuples from certifications
    """
    trust_score = score_data.get("trust_score", 0)
    risk_flags  = score_data.get("risk_flags", [])
    features    = score_data.get("feature_snapshot", {})
    risk_label, risk_color = _risk_level(trust_score)

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    pdf.set_margins(20, 20, 20)

    # ── Header bar ──────────────────────────────────────────────────── #
    pdf.set_fill_color(15, 15, 30)
    pdf.rect(0, 0, 210, 28, style="F")

    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(255, 255, 255)
    pdf.set_xy(20, 8)
    pdf.cell(0, 10, "SOURCEGUARD", ln=False)

    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(160, 160, 200)
    pdf.set_xy(20, 18)
    pdf.cell(0, 6, "Supplier Due Diligence Report — Confidential", ln=True)

    # Date top-right
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(140, 140, 170)
    pdf.set_xy(130, 10)
    pdf.cell(60, 6, f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}", align="R")

    pdf.set_xy(20, 36)

    # ── Supplier identity ────────────────────────────────────────────── #
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(20, 20, 40)
    pdf.cell(0, 10, supplier.get("name", "Unknown"), ln=True)

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(80, 80, 100)
    meta_parts = []
    if supplier.get("country"):
        meta_parts.append(supplier["country"])
    if supplier.get("id"):
        meta_parts.append(f"ID: {supplier['id']}")
    if supplier.get("source"):
        meta_parts.append(f"Source: {supplier['source']}")
    pdf.cell(0, 6, "  ·  ".join(meta_parts), ln=True)
    pdf.ln(6)

    # ── Trust score block ────────────────────────────────────────────── #
    # Score circle (simulated with filled rect)
    r, g, b = risk_color
    pdf.set_fill_color(r, g, b)
    pdf.set_draw_color(r, g, b)
    pdf.rect(20, pdf.get_y(), 50, 30, style="F")

    pdf.set_font("Helvetica", "B", 26)
    pdf.set_text_color(255, 255, 255)
    y_score = pdf.get_y()
    pdf.set_xy(20, y_score + 4)
    pdf.cell(50, 14, f"{trust_score:.0f}", align="C", ln=False)

    pdf.set_font("Helvetica", "B", 8)
    pdf.set_xy(20, y_score + 18)
    pdf.cell(50, 8, "/ 100  TRUST SCORE", align="C", ln=False)

    # Risk label beside score
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(r, g, b)
    pdf.set_xy(76, y_score + 8)
    pdf.cell(0, 10, risk_label, ln=True)

    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(90, 90, 110)
    pdf.set_xy(76, y_score + 18)
    pdf.cell(0, 8, f"Risk probability: {score_data.get('risk_probability', 0)*100:.1f}%", ln=True)

    pdf.set_y(y_score + 36)
    pdf.ln(4)

    # ── Divider ──────────────────────────────────────────────────────── #
    def divider():
        pdf.set_draw_color(220, 220, 235)
        pdf.line(20, pdf.get_y(), 190, pdf.get_y())
        pdf.ln(5)

    def section(title):
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(99, 102, 241)
        pdf.cell(0, 6, title.upper(), ln=True)
        pdf.ln(1)

    # ── Shipment summary ─────────────────────────────────────────────── #
    divider()
    section("Shipment Summary")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(40, 40, 60)

    cols = [
        ("Total Shipments",    supplier.get("shipment_count", "—")),
        ("Avg Monthly",        supplier.get("avg_monthly_shipments", "—")),
        ("Distinct Buyers",    supplier.get("total_buyers", "—")),
        ("Last Shipment",      str(supplier.get("last_shipment_date", "—"))),
    ]
    col_w = 42
    for label, val in cols:
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(110, 110, 130)
        pdf.cell(col_w, 5, label, ln=False)
    pdf.ln()
    for label, val in cols:
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(20, 20, 40)
        pdf.cell(col_w, 7, str(val), ln=False)
    pdf.ln(10)

    # ── Certifications ───────────────────────────────────────────────── #
    divider()
    section("Certification Status")
    if not certs:
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(180, 60, 60)
        pdf.cell(0, 6, "No certifications on file.", ln=True)
    else:
        for source, status, valid_until in certs:
            color = (74, 180, 100) if status == "valid" else (200, 80, 80)
            pdf.set_fill_color(*color)
            pdf.set_text_color(255, 255, 255)
            pdf.set_font("Helvetica", "B", 8)
            pdf.cell(24, 6, source.upper(), fill=True, ln=False)
            pdf.set_fill_color(240, 240, 248)
            pdf.set_text_color(40, 40, 60)
            pdf.set_font("Helvetica", "", 8)
            expiry = f"  valid until {str(valid_until)[:10]}" if valid_until and status == "valid" else ""
            pdf.cell(80, 6, f"  {status.capitalize()}{expiry}", fill=True, ln=True)
            pdf.ln(2)
    pdf.ln(2)

    # ── Risk flags ───────────────────────────────────────────────────── #
    divider()
    section("SHAP Risk Flags")
    if not risk_flags:
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(60, 160, 90)
        pdf.cell(0, 6, "No risk flags detected. Supplier meets all baseline criteria.", ln=True)
    else:
        for flag in risk_flags:
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(180, 60, 60)
            pdf.cell(4, 6, "▸", ln=False)
            pdf.set_text_color(40, 40, 60)
            pdf.multi_cell(0, 6, flag)
    pdf.ln(2)

    # ── Feature snapshot ─────────────────────────────────────────────── #
    if features:
        divider()
        section("Model Feature Snapshot")
        pdf.set_font("Helvetica", "", 8)
        col1_w = 90
        items = list(features.items())
        for i in range(0, len(items), 2):
            k1, v1 = items[i]
            pdf.set_text_color(110, 110, 130)
            pdf.cell(col1_w, 5, k1.replace("_", " ").title(), ln=False)
            if i + 1 < len(items):
                k2, v2 = items[i + 1]
                pdf.cell(col1_w, 5, k2.replace("_", " ").title(), ln=False)
            pdf.ln()
            pdf.set_text_color(20, 20, 40)
            pdf.set_font("Helvetica", "B", 8)
            pdf.cell(col1_w, 5, f"{v1:.3f}" if isinstance(v1, float) else str(v1), ln=False)
            if i + 1 < len(items):
                pdf.cell(col1_w, 5, f"{v2:.3f}" if isinstance(v2, float) else str(v2), ln=False)
            pdf.ln(7)
            pdf.set_font("Helvetica", "", 8)

    # ── Footer ───────────────────────────────────────────────────────── #
    pdf.set_y(-20)
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(160, 160, 180)
    pdf.multi_cell(
        0, 4,
        "This report is generated automatically by SourceGuard and is intended for internal due diligence use only. "
        "It is not a substitute for a formal audit. SourceGuard makes no warranty as to the accuracy of third-party data sources.",
        align="C",
    )

    return pdf.output()
