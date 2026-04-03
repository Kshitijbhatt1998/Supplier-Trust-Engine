# Textile Supplier Trust Engine

**DataVibe** — Supplier fulfillment risk scoring for trade finance and supply chain intelligence.

Transforms raw customs data, certification records, and B2B signals into a structured Trust Score (0–100) with SHAP-driven risk flags.

---

## Architecture

```
ImportYeti (Playwright) ──┐
OEKO-TEX (Playwright)  ──┤──► DuckDB ──► Feature Engineering ──► LightGBM ──► FastAPI
GOTS (Playwright)      ──┘
```

---

## Setup

### 1. Clone & install

```bash
git clone https://github.com/YOUR_USERNAME/textile-supplier-trust-engine
cd textile-supplier-trust-engine

python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

pip install -r requirements.txt
playwright install chromium
```

### 2. Configure

```bash
cp .env.example .env
# Fill in IMPORTYETI_EMAIL and IMPORTYETI_PASSWORD
```

Create a free ImportYeti account at https://www.importyeti.com

### 3. Run the pipeline

```bash
# Full pipeline (scrape → verify → score)
python run_pipeline.py --scrape   # Step 1: collect supplier data
python run_pipeline.py --verify   # Step 2: verify certifications

# Label ~30-50 suppliers manually
jupyter notebook notebooks/label_suppliers.ipynb

# Train model + score all suppliers
python run_pipeline.py --train
python run_pipeline.py --score
```

### 4. Start the API

```bash
uvicorn api.main:app --reload --port 8000
```

API docs: http://localhost:8000/docs

**Score a supplier:**
```bash
curl -X POST http://localhost:8000/score \
  -H "Content-Type: application/json" \
  -d '{"supplier_name": "Welspun India"}'
```

---

## Fixing Scrapy Selectors (Important)

ImportYeti's DOM changes. After running the scraper, if fields are `null`:

1. Open any `/company/` page in Chrome
2. Open DevTools → Elements tab
3. Find the company name `<h1>` → right-click → Copy selector
4. Update the corresponding selector in `pipeline/spiders/importyeti_scraper.py`

Key selectors to verify:
| Field | Location in `_scrape_supplier()` |
|-------|----------------------------------|
| Company name | `await self._safe_text(page, "h1")` |
| Country | `[data-testid='supplier-country']` |
| Shipment count | `[data-testid='total-shipments']` |
| HS codes | `[data-testid='hs-code-tag']` |
| Buyers | `[data-testid='buyer-name']` |

Set `HEADLESS=false` in `.env` to watch the browser in real-time while debugging.

---

## Project Structure

```
textile-supplier-trust-engine/
├── pipeline/
│   ├── spiders/
│   │   └── importyeti_scraper.py     # Playwright-based ImportYeti scraper
│   ├── verifiers/
│   │   └── certification_verifier.py # OEKO-TEX + GOTS async verifier
│   └── storage/
│       └── db.py                     # DuckDB schema + upsert helpers
├── model/
│   ├── features.py                   # Feature engineering (15 features)
│   └── scorer.py                     # LightGBM + SHAP trust scoring
├── api/
│   └── main.py                       # FastAPI scoring endpoint
├── notebooks/
│   └── label_suppliers.ipynb         # Manual labeling for training data
├── data/                             # DuckDB file + labeled CSV (gitignored)
├── run_pipeline.py                   # End-to-end orchestrator
├── requirements.txt
└── .env.example
```

---

## Trust Score Interpretation

| Score | Meaning |
|-------|---------|
| 80–100 | Low risk — strong manufacturer signals |
| 60–79  | Moderate — verify specific flags |
| 40–59  | Elevated risk — proceed with caution |
| 0–39   | High risk — likely middleman or compliance gaps |

Risk flags (SHAP-driven) explain *why* a supplier scored low — e.g.:
- "High customer concentration (captive factory risk)"
- "No valid certifications found"
- "Low shipment volume vs. industry peers"
