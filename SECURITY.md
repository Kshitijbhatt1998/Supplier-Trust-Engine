# Security Policy

The Supplier Trust Engine team takes the security of our procurement intelligence infrastructure seriously. We appreciate the community's efforts in identifying and reporting vulnerabilities.

## Supported Versions

| Version | Supported |
| ------- | --------- |
| 1.x     | Yes       |
| < 1.0   | No        |

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

If you discover a potential security flaw, please report it via one of the following channels:

1. **GitHub Private Reporting:** Use the [Private Vulnerability Reporting](https://github.com/Kshitijbhatt1998/Supplier-Trust-Engine/security/advisories/new) feature on GitHub.
2. **Email:** Send a detailed report to `security@supplier-trust-engine.io`.

### What to include in your report

- A description of the vulnerability and its potential impact.
- Step-by-step instructions to reproduce the issue (PoC scripts or screenshots encouraged).
- Environment details (OS, Python version, Node.js version) if relevant.

### What to expect

- **Acknowledgment:** Within 48–72 hours.
- **Triage:** We will investigate and may contact you for clarification.
- **Resolution:** Patch within 14–30 days depending on severity.
- **Recognition:** With your permission, we will credit you in the release notes.

## Disclosure Policy

We follow coordinated disclosure. Please give us reasonable time to remediate before public disclosure.

---

## Security Design

This section documents the security controls built into the Supplier Trust Engine so that operators and auditors can verify the posture of a running deployment.

---

### Authentication & Authorization

The API uses two distinct security headers:

| Header | Scope | Backed by |
| ------ | ----- | --------- |
| `X-API-Key` | All mutating agent endpoints (`POST /v1/score`, `POST /v1/procure/evaluate`, `POST /v1/resolver/feedback`) | `API_KEY` env var |
| `X-Admin-Token` | All admin dashboard endpoints (`/v1/admin/*`) | `ADMIN_TOKEN` env var |

**Startup enforcement:** `api/auth.py` raises `ValueError` at module import time if either `API_KEY` or `ADMIN_TOKEN` is not set in the environment. The server will not start with a missing or empty credential — there is no insecure default fallback.

Read-only dashboard endpoints (`GET /v1/suppliers`, `GET /v1/stats`, `GET /v1/health`) require no key but are still covered by rate limiting.

**API keys are never exposed to the browser.** The nginx reverse proxy injects `X-API-Key` server-side at request time via `envsubst`. `VITE_API_KEY` and `VITE_ADMIN_TOKEN` in `dashboard/.env.local` are used for local development only and must never be set to production credentials, as Vite bundles `VITE_*` variables into the client JS at build time.

---

### Transport Security

- All external traffic must be terminated at TLS 1.2+ by the upstream load balancer or nginx before reaching the API container.
- The `docker-compose.yml` provided for local development binds ports to `127.0.0.1` only.
- `Strict-Transport-Security` (`max-age=31536000; includeSubDomains`) is set automatically when the API detects `request.url.scheme == "https"`.

---

### Security Headers

Every HTTP response includes the following headers, injected by the `add_security_headers` middleware in `api/main.py`:

| Header | Value |
| ------ | ----- |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` (HTTPS only) |

---

### CORS

- The `Access-Control-Allow-Origin` header is derived from the `ALLOWED_ORIGINS` environment variable (comma-separated list).
- `*` (wildcard) is never used. Deployments default to `http://localhost,http://localhost:80` and must be narrowed to the actual dashboard origin in production.
- `allow_credentials` is `False` — authentication is token-in-header, not cookie-based.

---

### Rate Limiting

All API endpoints are guarded by `slowapi` per source IP:

| Endpoint group | Limit |
| -------------- | ----- |
| `GET /v1/health`, `GET /v1/stats` | 60 / minute |
| `GET /v1/suppliers` | 5 / minute (full-table export risk) |
| `GET /v1/supplier/{id}` | 30 / minute |
| `POST /v1/score` | 10 / minute |
| `POST /v1/procure/evaluate` | 5 / minute |
| `POST /v1/resolver/feedback` | 20 / minute |
| `GET /v1/admin/review-queue` | 10 / minute |
| `POST /v1/admin/alias/action` | 10 / minute |
| `GET /v1/admin/audit-logs` | 20 / minute |
| `POST /v1/admin/audit/undo` | 5 / minute |

Exceeding the limit returns HTTP 429 with a `Retry-After` header.

---

### Input Validation

- All request bodies are validated by Pydantic v2 models with explicit `Field` constraints (`min_length`, `max_length`, `ge`, `le`).
- List fields (`required_certs`, `country_prefer`, `country_exclude`) are capped to 10–20 items, with each element truncated to 100 characters.
- `AdminActionRequest.alias_ids` is capped at 200 entries to prevent bulk-DoS.
- The `category` query parameter on admin endpoints is validated against `SupplierCategory(str, Enum)` — only `"textile"` and `"chemical"` are accepted; any other value returns HTTP 422.
- SQL parameters are always passed as positional bind parameters (`?`); no string interpolation is used in any query.

---

### Error Handling

- A global `@app.exception_handler(Exception)` catches all unhandled errors, logs them server-side via `loguru`, and returns a generic `{"detail": "Internal server error"}` to the caller.
- The audit undo endpoint catches exceptions individually, logs the full error with `audit_id` context, and returns `"Undo operation failed. Check server logs."` — internal state is never serialised into the HTTP response.
- Stack traces, file paths, database error messages, and internal model details are never surfaced in API responses.

---

### Audit Log & Undo Safety

The `admin_audit_log` table records every verify/reject action with:

- A full **snapshot** of the affected alias rows (version-tagged JSON) to enable safe reversal.
- A **24-hour undo window** — attempts outside this window are rejected with HTTP 400.
- **Snapshot schema validation** before any restore: the undo endpoint checks `version == 1` and verifies all required keys are present in every snapshot item before executing any database writes. A malformed or tampered snapshot returns HTTP 400 rather than a partial restore.
- All undo operations run inside an explicit `BEGIN TRANSACTION` / `COMMIT` block; a `ROLLBACK` is issued on any exception.

---

### Entity Resolution Anti-Pollution Controls

The entity resolution layer includes several defences against data quality attacks:

**Adaptive Thresholds (Laplace Smoothing)**
Each canonical entity maintains a `resolver_config` view that tracks rejection and verification counts. The fuzzy match threshold tightens automatically as rejections accumulate:

```text
threshold = min(BASE + (rejections + 1) / (rejections + verifications + 2) × PENALTY, MAX)
```

A newly seeded canonical starts at the Laplace-neutral rate (≈ 0.5), meaning the threshold is already elevated before any human feedback. This prevents a cold-start flood of bad aliases.

**Noise Priming**
Known misspellings and abbreviation variants (e.g. `HDPE GRANULS`) are pre-seeded into `entity_rejections` with 10 rejection entries each during `pipeline/ingest_polymers.py`. This forces a strict threshold from day one for chemically ambiguous terms.

**Role Shield**
Logistics surrogates appearing as manufacturer names — strings containing `C/O`, `CARE OF`, `VIA`, or `BY` clusters — are:

1. Stripped of the trailing logistics tokens before fuzzy matching.
2. Flagged with `is_role_warning: true` in the review queue response.
3. Pre-seeded as `entity_rejections` for known trader-manufacturer pairs (e.g. `XYZ LOGISTICS` → `sabic-global` with reason `role_pollution_carrier`).

A trader pre-seeded in rejections will return `supplier_id: null` even if its name partially matches a canonical.

**CAS Exact Match Bypass**
Chemical names containing a valid CAS Registry Number (checksum-validated per ECHA rules) are routed directly to the CAS-anchored canonical — they never enter the fuzzy pipeline. This eliminates false positives on numeric chemical identifiers.

---

### Credential & Secrets Management

- `API_KEY` and `ADMIN_TOKEN` must be strong random values (minimum 32 hex bytes; generate with `openssl rand -hex 32` or `python -c "import secrets; print(secrets.token_hex(32))"`).
- `.env` and `dashboard/.env.local` are excluded from version control via `.gitignore`.
- `.env.example` contains only placeholder values — it must never hold real credentials.
- The ImportYeti scraper session file (`data/.importyeti_session.json`) is written with `chmod 600` (owner read/write only) and is excluded from version control.

---

### Data Privacy

- The engine stores publicly available trade data (UN Comtrade), publicly listed certification statuses (OEKO-TEX, GOTS), and shipping manifests sourced from public import/export records (ImportYeti).
- No personally identifiable information (PII) is collected or stored.
- The DuckDB file (`data/trust_engine.duckdb`) is excluded from version control via `.gitignore` and must not be committed or pushed.

---

### Dependency Scanning

- GitHub Dependabot is configured (`.github/dependabot.yml`) to scan both `pip` and `npm` dependency graphs weekly and open automated PRs for vulnerable or outdated packages.
- Python and Node.js dependencies are pinned to exact versions in `requirements.txt` and `dashboard/package-lock.json` respectively.

---

Last Updated: April 7, 2026
