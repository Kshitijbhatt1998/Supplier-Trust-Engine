# Supplier Trust Engine

**DataVibe** — AI-powered supplier due diligence for autonomous procurement.

Transforms raw customs data, certification records, and B2B trade signals into a structured **Trust Score (0–100)** with SHAP-driven, human-readable risk flags. Built for AI procurement agents and trade intelligence teams who need to evaluate hundreds of suppliers in seconds — without a compliance team.

---

## The Problem

Global B2B procurement is broken:

- **30–40% of textile suppliers** presenting themselves as manufacturers are actually middlemen or brokers — adding cost, risk, and opacity to the supply chain.
- Buyers spend weeks manually verifying certifications (GOTS, OEKO-TEX), cross-checking trade records, and calling references — only to still get burned.
- Emerging **AI procurement agents** (autonomous buying systems) have no structured trust layer to filter supplier databases before placing orders.

---

## What It Does

The Supplier Trust Engine automates the entire supplier vetting workflow:

1. **Scrapes** supplier shipment history from ImportYeti (US customs manifests)
2. **Verifies** GOTS and OEKO-TEX certifications directly from issuing-body portals
3. **Cross-references** claimed volumes against UN Comtrade national trade statistics
4. **Engineers 17 features** that distinguish real manufacturers from middlemen
5. **Scores** every supplier 0–100 with a LightGBM model + SHAP explainability
6. **Resolves** supplier names to canonical entities using adaptive fuzzy matching with Laplace-smoothed thresholds
7. **Exposes** scores via a production FastAPI — ready for AI agent consumption

The result: any AI agent or procurement team can call `POST /v1/procure/evaluate` with criteria and receive a ranked, explainable shortlist of vetted suppliers in milliseconds.

---

## Business Model

### Who Pays

| Customer | Pain | Willingness to Pay |
|:---|:---|:---|
| **AI procurement startups** | Their agents need a trust layer before executing orders | API subscription — $500–$5,000/mo |
| **Fashion/retail sourcing teams** | Currently pay $10k+/yr for manual audits | SaaS seat license — $200–$800/user/mo |
| **Trade finance / factoring firms** | Need supplier risk scores before advancing cash against invoices | Per-score API calls — $0.50–$5.00/score |
| **Supply chain compliance SaaS** | Want to embed trust signals into existing platforms | White-label data license — $2k–$20k/mo |

### Revenue Streams

```
Tier 1 — API Access       $299/mo    → 1,000 scores/mo
Tier 2 — Growth           $999/mo    → 10,000 scores/mo + procurement endpoint
Tier 3 — Enterprise       Custom     → Dedicated instance, custom data sources, SLA
Data License              Custom     → Bulk trust scores for platforms (Tier 1 customer → Shopify, Faire, etc.)
```

### Market Size

- Global supply chain risk management market: **$19.3B by 2028** (CAGR 15.1%)
- Textile/apparel sourcing software: **$3.2B** addressable
- AI procurement automation: fastest-growing sub-segment, 3-5 new funded startups/quarter

### Competitive Moat

| Competitor | Gap |
|:---|:---|
| Panjiva / ImportGenius | Raw data, no scoring, no AI agent API |
| Sourcemap | Mapping focus, not risk scoring |
| Sedex / EcoVadis | Survey-based, slow, expensive, no API |
| **This product** | Real-time scored API + SHAP explanations + AI-agent-native design |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        DATA SOURCES                         │
│                                                             │
│  ImportYeti ──► US Customs manifests (shipment history)     │
│  OEKO-TEX   ──► Certification portal (label check API)      │
│  GOTS       ──► Certified facilities database               │
│  UN Comtrade──► National trade statistics (export volumes)  │
└──────────────────────────┬──────────────────────────────────┘
                           │  Playwright async scrapers
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                    DuckDB  (local/volume)                    │
│  suppliers │ certifications │ shipments │ trade_stats        │
│  entity_aliases │ entity_rejections │ trust_scores           │
│  admin_audit_log │ resolver_config (view)                    │
└──────────────────────────┬──────────────────────────────────┘
                           │
              ┌────────────┴────────────┐
              ▼                         ▼
     Feature Engineering         Entity Resolution
     (17 signals, textile)       (adaptive fuzzy matching
                                  + CAS exact match)
              │                         │
              └────────────┬────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────┐
│              LightGBM Classifier  +  SHAP Explainer         │
│  Input:  17 features per supplier (textile category only)   │
│  Output: risk_probability (0–1) → trust_score (0–100)      │
│          + top 3 SHAP risk flags in plain English           │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│          FastAPI  /v1/  (rate-limited, API-key auth)        │
│                                                             │
│  GET  /v1/health              Healthcheck                   │
│  GET  /v1/stats               Dashboard aggregate counts    │
│  GET  /v1/suppliers           Filtered supplier list        │
│  GET  /v1/supplier/{id}       Full trust profile            │
│  POST /v1/score               Score by name or ID           │
│  POST /v1/procure/evaluate    AI Decision Engine            │
│  POST /v1/resolver/feedback   Human-in-the-loop feedback    │
│  GET  /v1/admin/review-queue  Admin alias review queue      │
│  POST /v1/admin/alias/action  Bulk verify / reject aliases  │
│  GET  /v1/admin/audit-logs    Action history feed           │
│  POST /v1/admin/audit/undo    Snapshot-based reversal       │
└──────────────────────────┬──────────────────────────────────┘
                           │
               ┌───────────┴────────────┐
               ▼                        ▼
        React Dashboard          AI Procurement Agent
        (nginx, port 80)         (any LLM / agentic system)
```

---

## Trust Score Features (17 signals)

> Applies to textile suppliers only. Chemical/polymer suppliers use manually-seeded trust scores.

| Feature | What It Measures | Middleman Signal |
|:---|:---|:---|
| `years_active` | Business maturity from first shipment date | < 2 years → high risk |
| `days_since_last_shipment` | Operational recency | > 180 days → inactive |
| `customer_concentration_ratio` | 1 / distinct buyers — captive factory risk | 1 buyer → ratio = 1.0 |
| `hs_code_count` | Number of product codes shipped | Too many or too few |
| `hs_chapter_diversity` | Number of distinct HS chapters | Middlemen spread wide |
| `shipment_frequency_score` | Monthly shipments normalized by years active | Low = broker |
| `certification_score` | Weighted GOTS (2pt) + OEKO-TEX (1pt) | 0 = no verified certs |
| `has_any_valid_cert` | Binary: any live certification | 0 = no proof of standards |
| `has_expired_cert` | Lapsed certifications | Compliance has slipped |
| `is_high_volume_shipper` | Above-median shipment count | Low = likely intermediary |
| `country_risk_score` | Country-level manufacturing risk lookup | Proxy for compliance quality |
| `manifest_verification_score` | Claimed vs. verified shipments | Low = unsubstantiated claims |
| `national_market_share` | Supplier volume vs. UN Comtrade national export data | Implausibly high = fraud |
| `shipment_count` | Total shipments ever recorded | Raw volume signal |
| `avg_monthly_shipments` | Operational cadence | Very low = broker |
| `total_buyers` | Number of distinct buyer relationships | 1–2 = captive factory |
| `valid_cert_count` | Total currently valid certifications | 0 = unverified |

---

## Entity Resolution

The engine resolves messy, real-world supplier names to canonical entities using a two-pass pipeline:

### Textile resolution
- Casefold + punctuation normalization → fuzzy token match (RapidFuzz)
- Adaptive threshold: `min(BASE + Laplace_rate × PENALTY, MAX)` — tightens automatically as rejections accumulate for a canonical
- Subsidiary and alias detection with `is_subsidiary_warning` flag

### Chemical resolution
- **CAS Registry Number** exact match (checksum-validated) — bypasses fuzzy entirely when a CAS is present in the name
- Longest-first abbreviation expansion (LLDPE before LDPE, PET before PE)
- Token order preserved — "Ethylene Oxide" ≠ "Oxide Ethylene"
- **Role Shield**: strips `C/O`, `VIA`, `BY` clusters from logistics surrogates; returns `is_role_warning: true` when the original name contained role noise

```
"SABIC C/O XYZ LOGISTICS"  →  canonical: sabic-global  (is_role_warning: true)
"9002-88-4"                →  canonical: cas-9002-88-4  (match_type: cas_exact)
"HDPE GRANULS"             →  candidate queued         (adaptive threshold blocked)
```

### Admin Review Dashboard
Unverified alias candidates surface in a prioritised queue with:
- **Priority score** = 0.4×volume + 0.3×trust + 0.3×match_score
- **Threshold badge** (green/yellow/red) showing the current adaptive threshold per canonical
- **CAS badge** (purple) linking to CAS Common Chemistry registry
- **Role Warning badge** (orange) on any alias containing `C/O`, `VIA`, or `BY`
- Bulk verify / reject with checkboxes + floating action bar
- Snapshot-based undo within 24 h via the Audit Feed

---

## Risk Flag Examples (SHAP-driven)

Every score includes plain-English explanations of *why* a supplier scored the way it did:

```json
{
  "trust_score": 18.5,
  "risk_flags": [
    "High customer concentration (captive factory risk)",
    "Missing or weak certifications",
    "Inactive recently (no recent shipments)"
  ]
}
```

---

## Project Structure

```
Supplier-Trust-Engine/
├── api/
│   ├── main.py                   # FastAPI app — all /v1/ endpoints + security middleware
│   ├── auth.py                   # X-API-Key + X-Admin-Token header validation
│   ├── resolver.py               # EntityResolver — adaptive fuzzy + CAS exact match
│   ├── chemical_normalizer.py    # CAS extraction, abbreviation expansion, Role Shield
│   └── decision_engine.py        # AI procurement decision engine
├── pipeline/
│   ├── spiders/
│   │   └── importyeti_scraper.py # Playwright-based ImportYeti scraper
│   ├── verifiers/
│   │   └── certification_verifier.py  # OEKO-TEX + GOTS async verifier
│   ├── ingest/
│   │   ├── comtrade_client.py    # UN Comtrade trade stats ingestion
│   │   └── manifest_scraper.py   # Bill of lading manifest verification
│   ├── ingest_polymers.py        # Chemical/polymer seed data + Role Shield priming
│   ├── entity_resolution.py      # resolve_and_upsert helper for scraper output
│   └── storage/
│       └── db.py                 # DuckDB schema, migrations, views, upsert helpers
├── model/
│   ├── features.py               # Feature engineering (17 signals, textile only)
│   ├── scorer.py                 # LightGBM training + SHAP scoring
│   ├── trust_model.pkl           # Trained model artifact
│   └── shap_explainer.pkl        # SHAP TreeExplainer artifact
├── dashboard/
│   ├── src/
│   │   ├── App.jsx               # Main dashboard shell
│   │   ├── api.js                # API client (proxied via nginx/Vite)
│   │   └── components/
│   │       ├── StatGrid.jsx              # 4-card KPI summary
│   │       ├── SupplierTable.jsx         # Filterable trust score table
│   │       ├── SupplierModal.jsx         # Full supplier detail panel
│   │       ├── ProcurementSimulator.jsx  # Live AI decision engine UI
│   │       └── AdminDashboard.jsx        # Alias review queue + audit feed
│   ├── Dockerfile                # Multi-stage: node build → nginx serve
│   └── nginx.conf                # Reverse proxy + API key injection
├── data/
│   ├── seed_suppliers.py         # 50 synthetic suppliers for dev/demo
│   └── labeled_suppliers.csv     # Binary risk labels for training
├── tests/
│   ├── test_smoke.py             # DB schema, upsert idempotency, feature engineering
│   ├── test_admin_api.py         # Admin queue, verify/reject, bulk, 403, category filter
│   ├── test_active_learning.py   # Adaptive threshold dynamics (Laplace smoothing)
│   ├── test_chemical_normalizer.py # CAS extraction, abbreviation expansion, noise stripping
│   └── test_role_shield.py       # C/O stripping, surrogate flag, resolver warning
├── scripts/
│   └── phase3_ingest.py          # Comtrade + manifest ingestion orchestrator
├── Dockerfile                    # API image (python:3.10-slim + libgomp1)
├── docker-compose.yml            # Two-service stack (api + dashboard)
├── entrypoint.sh                 # Auto-seeds + trains on first boot
├── run_pipeline.py               # CLI orchestrator for all pipeline steps
└── requirements.txt
```

---

## API Reference

All endpoints are under `/v1/`.

| Endpoint | Auth | Description |
|:---|:---|:---|
| `GET /v1/health` | None | Healthcheck |
| `GET /v1/stats` | None | Dashboard aggregate counts |
| `GET /v1/suppliers` | None | Filtered supplier list (5/min) |
| `GET /v1/supplier/{id}` | None | Full trust profile |
| `POST /v1/score` | `X-API-Key` | Score by name or ID |
| `POST /v1/procure/evaluate` | `X-API-Key` | AI Decision Engine |
| `POST /v1/resolver/feedback` | `X-API-Key` | Confirm / reject a resolution |
| `GET /v1/admin/review-queue` | `X-Admin-Token` | Prioritised alias review queue |
| `POST /v1/admin/alias/action` | `X-Admin-Token` | Bulk verify or reject aliases |
| `GET /v1/admin/audit-logs` | `X-Admin-Token` | Recent action history |
| `POST /v1/admin/audit/undo` | `X-Admin-Token` | Snapshot-based undo (24 h window) |

### `GET /v1/health`
```json
{ "status": "ok", "service": "textile-trust-engine", "suppliers_in_db": 50 }
```

### `POST /v1/score` _(API key required)_
```bash
curl -X POST https://your-domain.com/api/v1/score \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"supplier_name": "Welspun India"}'
```
```json
{
  "supplier_id": "welspun-india-ltd",
  "supplier_name": "Welspun India Ltd",
  "country": "India",
  "trust_score": 100.0,
  "risk_probability": 0.0,
  "risk_flags": [],
  "certification_status": {
    "gots":    { "status": "valid", "valid_until": "2026-03-15" },
    "oekotex": { "status": "valid", "valid_until": "2025-11-30" }
  },
  "shipment_summary": {
    "total_shipments": 412,
    "avg_monthly": 28.5,
    "total_buyers": 14,
    "last_shipment": "2025-12-01"
  },
  "resolution_metadata": {
    "match_type": "fuzzy",
    "match_score": 96.4,
    "canonical_name": "Welspun India Ltd",
    "low_confidence": false
  }
}
```

### `POST /v1/procure/evaluate` _(API key required)_

The **AI Decision Engine** — send procurement criteria, receive a ranked shortlist with rationale.

```bash
curl -X POST https://your-domain.com/api/v1/procure/evaluate \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "category": "organic cotton tote bags",
    "min_trust_score": 80,
    "required_certs": ["gots"],
    "country_prefer": ["India", "Turkey"],
    "country_exclude": [],
    "max_days_inactive": 180,
    "max_results": 3
  }'
```

### `GET /v1/admin/review-queue` _(Admin token required)_
```bash
curl "https://your-domain.com/api/v1/admin/review-queue?category=chemical" \
  -H "X-Admin-Token: your-admin-token"
```
```json
[
  {
    "id": "abc123",
    "alias_name": "SABIC C/O XYZ LOGISTICS",
    "canonical_id": "sabic-global",
    "canonical_name": "SABIC Innovative Plastics",
    "match_score": 91.2,
    "priority_score": 0.7340,
    "adaptive_threshold": 91.0,
    "rejection_count": 3,
    "verification_count": 1,
    "cas_number": null,
    "is_role_warning": true
  }
]
```

### `POST /v1/admin/alias/action` _(Admin token required)_
```json
{ "alias_ids": ["abc123", "def456"], "action": "verify", "reason_code": "confirmed_manufacturer" }
```

---

## Quickstart — Local Development

### Prerequisites

- Python 3.10+
- Node.js 20+
- [ImportYeti](https://www.importyeti.com) free account

### 1. Clone & install

```bash
git clone https://github.com/Kshitijbhatt1998/Supplier-Trust-Engine
cd Supplier-Trust-Engine

python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS/Linux

pip install -r requirements.txt
playwright install chromium
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — fill in all required values (see Environment Variables below)
```

Generate strong secrets:
```bash
python -c "import secrets; print(secrets.token_hex(32))"  # run twice — one for API_KEY, one for ADMIN_TOKEN
```

### 3. Seed chemical/polymer data

```bash
python -m pipeline.ingest_polymers
# Seeds SABIC, Reliance, ExxonMobil, Formosa + CAS-anchored PE/PVC/PP
# Primes Role Shield rejections for known trader-manufacturer clusters
```

### 4. Run the pipeline

```bash
# Option A — Quick demo with synthetic data (no scraping)
python run_pipeline.py --seed --train --score

# Option B — Full live pipeline
python run_pipeline.py --scrape   # Collect real supplier data from ImportYeti
python run_pipeline.py --verify   # Verify GOTS + OEKO-TEX certifications
# Label suppliers in notebooks/label_suppliers.ipynb
python run_pipeline.py --train    # Train LightGBM model (textile suppliers only)
python run_pipeline.py --score    # Score all suppliers
```

### 5. Start the API

```bash
uvicorn api.main:app --reload --port 8000
# Docs: http://localhost:8000/docs
```

> The server will refuse to start if `API_KEY` or `ADMIN_TOKEN` are not set in the environment.

### 6. Start the dashboard

```bash
cd dashboard
cp .env.local.example .env.local  # or create manually
# Set VITE_API_KEY and VITE_ADMIN_TOKEN to match your .env values
npm install
npm run dev
# Dashboard: http://localhost:5173
```

---

## Quickstart — Docker (Production)

```bash
cp .env.example .env
# Set API_KEY, ADMIN_TOKEN, ALLOWED_ORIGINS, SENTRY_DSN (optional) in .env

docker compose up --build
# Dashboard → http://localhost:80
# API docs  → http://localhost:80/api/v1/docs (proxied)
```

On first boot, `entrypoint.sh` automatically seeds the database and trains the model if the volume is empty.

---

## Environment Variables

| Variable | Required | Description |
|:---|:---|:---|
| `API_KEY` | **Yes** | Secret key for protected endpoints (`X-API-Key` header). Server refuses to start if unset. |
| `ADMIN_TOKEN` | **Yes** | Secret key for admin dashboard endpoints (`X-Admin-Token` header). Server refuses to start if unset. |
| `IMPORTYETI_EMAIL` | For scraping | ImportYeti account email |
| `IMPORTYETI_PASSWORD` | For scraping | ImportYeti account password |
| `DB_PATH` | No | DuckDB file path (default: `data/trust_engine.duckdb`) |
| `ALLOWED_ORIGINS` | Production | Comma-separated allowed CORS origins (e.g. `https://yourdomain.com`) |
| `SENTRY_DSN` | Production | Sentry error tracking DSN |
| `HEADLESS` | No | `true`/`false` — show browser during scraping (default: `true`) |
| `REQUEST_DELAY_MIN` | No | Min scraper delay in seconds (default: `2.0`) |
| `REQUEST_DELAY_MAX` | No | Max scraper delay in seconds (default: `5.0`) |

---

## Security

| Control | Implementation |
|:---|:---|
| Authentication | `X-API-Key` on all POST endpoints; `X-Admin-Token` on all admin endpoints |
| Startup guard | `ValueError` raised at import if `API_KEY` or `ADMIN_TOKEN` env vars are unset |
| CORS | Env-configured allowlist — never `*` in production |
| Rate limiting | 60/min (public GET), 5/min (/suppliers), 10/min (score/admin), 5/min (procure/undo) |
| Input validation | Pydantic `Field(max_length)`, `Query(ge/le)`, list validators; `alias_ids` capped at 200 |
| Category enum | `SupplierCategory` enum validates `?category=` — rejects anything outside `textile`/`chemical` |
| Error handling | Global handler — 500s never expose stack traces; undo exceptions return generic message |
| Security headers | `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, HSTS (TLS only) |
| Session files | ImportYeti session cookies written with `chmod 600` |
| Audit undo | Snapshot schema validated (version + required keys) before any DB restore |
| Dependency scanning | GitHub Dependabot (pip + npm, weekly) |

See [SECURITY.md](SECURITY.md) for the full security design and vulnerability reporting process.

---

## Running Tests

```bash
# All tests
pytest tests/ -v

# Specific suites
pytest tests/test_smoke.py            # DB schema + feature engineering
pytest tests/test_admin_api.py        # Admin endpoints (priority queue, bulk action, auth)
pytest tests/test_active_learning.py  # Adaptive threshold / Laplace smoothing
pytest tests/test_chemical_normalizer.py  # CAS extraction, abbreviation expansion
pytest tests/test_role_shield.py      # C/O stripping, surrogate flag, Role Shield
```

---

## Fixing Scraper Selectors

ImportYeti's DOM changes periodically. If scraped fields return `null`:

1. Open any `importyeti.com/company/` page in Chrome
2. DevTools → Elements → find the field → Copy selector
3. Update in [pipeline/spiders/importyeti_scraper.py](pipeline/spiders/importyeti_scraper.py) under `_scrape_supplier()`

Set `HEADLESS=false` in `.env` to watch the browser live while debugging.

Key selectors to verify:

| Field | Current selector |
|:---|:---|
| Company name | `h1` |
| Country | `[data-testid='supplier-country']` |
| Shipment count | `[data-testid='total-shipments']` |
| HS codes | `[data-testid='hs-code-tag']` |
| Buyers | `[data-testid='buyer-name']` |

---

## Trust Score Reference

| Score | Risk Level | Meaning |
|:---|:---|:---|
| 80–100 | Low | Strong manufacturer signals — safe to proceed |
| 60–79 | Moderate | Verify the specific flags before committing |
| 40–59 | Elevated | Proceed with caution — request additional documentation |
| 0–39 | High | Likely middleman or compliance gap — do not source |

---

## Roadmap

- [x] Admin Review Dashboard with adaptive threshold God View
- [x] Multi-industry support (textile + chemical/polymer)
- [x] CAS Registry Number exact-match resolver
- [x] Role Shield — C/O / VIA / BY surrogate detection
- [x] Snapshot-based audit undo
- [ ] GRS (Global Recycled Standard) certification verifier
- [ ] Chemical category LightGBM model (dedicated feature engineering)
- [ ] Supplier changelog — track score changes over time
- [ ] Webhook alerts when a supplier's score drops below threshold
- [ ] Shopify / Faire plugin for direct store integration
- [ ] Multi-tenant API with per-customer supplier databases
- [ ] Real-time scraping triggers (score on demand, not just batch)

---

## Tech Stack

| Layer | Technology |
|:---|:---|
| Scraping | Playwright (async, JS-rendered pages) |
| Storage | DuckDB (embedded analytical DB — no server required) |
| Feature engineering | Pandas + NumPy |
| ML model | LightGBM (gradient boosted trees) |
| Explainability | SHAP TreeExplainer |
| Entity resolution | RapidFuzz + Laplace-smoothed adaptive thresholds |
| Chemical normalization | CAS checksum validation, longest-first abbreviation expansion |
| API | FastAPI + slowapi (rate limiting) + Pydantic v2 |
| Frontend | React 18 + Vite 6 |
| Containerisation | Docker + Docker Compose (nginx reverse proxy) |
| Error tracking | Sentry |
| Dependency scanning | GitHub Dependabot |
| CI data | UN Comtrade API (national trade statistics) |

---

## License

MIT — use freely, attribution appreciated.

---

*Built by [DataVibe](https://github.com/Kshitijbhatt1998) · Supplier intelligence for the autonomous economy*
