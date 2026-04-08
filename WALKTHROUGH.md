# V2 Feature Walkthrough

This document walks through every V2 capability added in Phase 3 of the Supplier Trust Engine.
Run through each section in order — each builds on the previous one.

Prerequisites: API running locally (`uvicorn api.main:app --reload`), database seeded.

---

## 1. Chemical Industry Model

V2 ships a dedicated LightGBM regressor for chemical/polymer suppliers, with its own
feature engineering pipeline (`model/features_chemical.py`) and trained artifact.

### Retrain the chemical model

```bash
python model/train_chemical.py
# Outputs: model/chemical_trust_model.pkl + model/chemical_shap_explainer.pkl
```

### Score a chemical supplier via the API

```bash
curl -X POST http://localhost:8000/v1/score \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"supplier_id": "sabic-global"}'
```

Expected response shape:

```json
{
  "trust_score": 84.2,
  "risk_flags": ["Low CAS/Registry linkage in trade data"],
  "feature_snapshot": {
    "cas_linkage_score": 0.8,
    "grade_purity_index": 1.0,
    "regulatory_hub_score": 0.9,
    "frequency_stability": 0.7,
    "buyer_network_diversity": 0.4
  },
  "category": "chemical"
}
```

The scorer automatically selects the chemical model when the supplier's `category = 'chemical'`.

### Chemical features at a glance

| Feature | Signal |
|:---|:---|
| `cas_linkage_score` | HS codes starting with 28/29/38 → real producer |
| `grade_purity_index` | Reagent/USP grade keywords → advanced facility |
| `regulatory_hub_score` | US/DE/JP/CH jurisdiction → higher inspection baseline |
| `frequency_stability` | Consistent shipment cadence → stable manufacturer |
| `buyer_network_diversity` | Multiple buyers → not captive to single off-taker |

---

## 2. Temporal Intelligence — Score History & Watchlists

Every re-scoring run now persists a changelog when a supplier's score shifts by ≥ 1 point.

### Query score history for a supplier

```sql
SELECT old_score, new_score, reason_code, changed_at
FROM supplier_score_history
WHERE supplier_id = 'welspun-india-ltd'
ORDER BY changed_at DESC;
```

### Add a supplier to a tenant watchlist

```sql
INSERT INTO tenant_watchlists (tenant_id, supplier_id, private_note, is_monitored)
VALUES ('my-tenant-id', 'welspun-india-ltd', 'Key Q4 supplier — flag any score drop', TRUE);
```

Watchlisted suppliers can be polled or surfaced in a dedicated dashboard widget (future).

---

## 3. Real-Time Ingestion

### 3a. On-demand supplier refresh

Force a fresh scrape and re-score for a specific supplier without waiting for the next
batch run:

```bash
curl -X POST http://localhost:8000/v1/suppliers/welspun-india-ltd/refresh \
  -H "X-API-Key: $API_KEY"
```

```json
{
  "supplier_id": "welspun-india-ltd",
  "supplier_name": "Welspun India Ltd",
  "status": "refreshed",
  "trust_score": 97.3,
  "message": "Supplier data refreshed and re-scored successfully."
}
```

The endpoint:
1. Looks up the supplier's stored `raw_url` (e.g. `https://importyeti.com/company/welspun-india`)
2. Calls `ImportYetiScraper.scrape_single_company()` with a fresh Playwright session
3. Runs `resolve_and_upsert()` to update DuckDB
4. Re-engineers features and runs the correct model (textile or chemical)
5. Writes the new score and logs a `supplier_score_history` entry if the score changed

Rate limit: **2 requests/minute** per tenant (scraping is expensive).

### 3b. GRS certificate verification

Verify a Global Recycled Standard certificate number in real-time:

```bash
curl -X POST http://localhost:8000/v1/verify/grs \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"cert_number": "CU123456GRS", "supplier_id": "welspun-india-ltd"}'
```

```json
{
  "cert_number": "CU123456GRS",
  "status": "valid",
  "source": "Textile Exchange Integrity Database",
  "supplier_id": "welspun-india-ltd"
}
```

When `supplier_id` is provided, the result is persisted to the `certifications` table and
will be reflected in the next scoring run.

---

## 4. Tiered Rate Limiting

V2 replaces the flat per-route rate limits on tenant API endpoints with dynamic per-tier limits
resolved at request time via `auth.get_tier_rate_limit`.

| Tier | RPM |
|:---|:---|
| tier_1 | 20 |
| tier_2 | 100 |
| enterprise | 1000 |

The limit is enforced by slowapi after the tenant is resolved from the API key.
Monthly hard quotas (via the `usage_logs` table) remain in place on top of the RPM limit:

| Tier | Monthly quota |
|:---|:---|
| tier_1 | 1,000 calls |
| tier_2 | 10,000 calls |
| enterprise | unlimited |

A `429` response is returned when either limit is hit, with a `detail` message indicating
which limit was exceeded.

---

## 5. Webhook Alerts

Subscribe a tenant to score-drop notifications:

```sql
INSERT INTO webhooks (id, tenant_id, url, secret, event_types, is_active)
VALUES (
  'wh-001',
  'my-tenant-id',
  'https://your-endpoint.com/webhook',
  'your-webhook-secret',
  '["score_drop"]',
  TRUE
);
```

When the batch scorer detects a drop of ≥ 5 points, `webhook_worker.deliver_alerts()` fires
an HMAC-SHA256-signed POST to registered URLs:

```json
{
  "event": "score_drop",
  "timestamp": "2026-04-08T12:00:00",
  "data": {
    "supplier_id": "xyz-textiles",
    "old_score": 72.1,
    "new_score": 58.4
  }
}
```

Verify the signature with:

```python
import hmac, hashlib
expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
assert hmac.compare_digest(expected, request.headers["X-Vibe-Signature"])
```

---

## 6. Shopify Integration

Sync trust scores to your Shopify store's product vendor metadata:

```bash
curl -X POST "http://localhost:8000/v1/integrations/shopify/sync?shop_url=acme.myshopify.com&access_token=shpat_..." \
  -H "Authorization: Bearer $JWT_TOKEN"
```

```json
{
  "status": "success",
  "shop": "acme.myshopify.com",
  "vendors_found": 4,
  "vendors_matched": 3,
  "vendors": [
    {"vendor": "Welspun India", "trust_score": 97.3, "matched": true},
    {"vendor": "Shahi Exports",  "trust_score": 81.5, "matched": true},
    {"vendor": "Unknown Vendor Co", "trust_score": null, "matched": false}
  ]
}
```

The `ShopifyConnector` class in [api/plugins/shopify_connector.py](api/plugins/shopify_connector.py)
contains clearly marked `# TODO` blocks for wiring in the real Shopify Admin API calls once
production credentials are available. The mockup returns a realistic response so the integration
can be fully tested end-to-end without live credentials.

---

## 7. Complete V2 Component Checklist

| Component | File | Status |
|:---|:---|:---|
| Chemical feature engineering | `model/features_chemical.py` | Done |
| Chemical training script | `model/train_chemical.py` | Done |
| Chemical model artifact | `model/chemical_trust_model.pkl` | Done |
| Multi-category scorer | `model/scorer.py` | Done |
| Score history table | `pipeline/storage/db.py` | Done |
| Tenant watchlists table | `pipeline/storage/db.py` | Done |
| History logging in scorer | `model/scorer.py` `_process_df` | Done |
| GRS Playwright verifier | `pipeline/verifiers/grs_verifier.py` | Done |
| `scrape_single_company` | `pipeline/spiders/importyeti_scraper.py` | Done |
| `POST /v1/suppliers/{id}/refresh` | `api/main.py` | Done |
| `POST /v1/verify/grs` | `api/main.py` | Done |
| Webhooks table | `pipeline/storage/db.py` | Done |
| Webhook worker | `api/webhook_worker.py` | Done |
| Dynamic tiered rate limiting | `api/auth.py` `get_tier_rate_limit` | Done |
| Shopify connector plugin | `api/plugins/shopify_connector.py` | Done |
