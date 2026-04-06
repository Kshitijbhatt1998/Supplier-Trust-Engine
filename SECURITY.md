# Security Policy

The Supplier Trust Engine team takes the security of our textile procurement infrastructure and data collection pipelines seriously. We appreciate the community's efforts in identifying and reporting vulnerabilities.

## Supported Versions

We currently provide security updates and patches for the following versions:

| Version | Supported          |
| ------- | ------------------ |
| 1.x     | :white_check_mark: |
| < 1.0   | :x:                |

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

If you discover a potential security flaw in the Supplier Trust Engine, please report it via one of the following channels:

1.  **Email:** Send a detailed report to `security@supplier-trust-engine.io`.
2.  **GitHub Private Reporting:** If enabled, use the [Private Vulnerability Reporting](https://github.com/Kshitijbhatt1998/Supplier-Trust-Engine/security/advisories/new) feature on GitHub.

### What to include in your report:
- A description of the vulnerability and its potential impact.
- Step-by-step instructions to reproduce the issue (PoC scripts or screenshots are highly encouraged).
- Any specific environment details (OS, Python version, Node.js version) relevant to the flaw.

### What to expect:
- **Acknowledgment:** You will receive a response within **48–72 hours** confirming we have received the report.
- **Triage:** Our team will investigate and validate the vulnerability. We may contact you for further clarification.
- **Resolution:** If accepted, we will work on a fix. We aim to release a patch within **14–30 business days**, depending on the severity.
- **Recognition:** With your permission, we will credit you for the discovery in our release notes or `SECURITY.md`.

## Disclosure Policy
We follow a coordinated disclosure policy. We ask researchers to give us a reasonable amount of time to remediate the issue before any public information is shared.

---

## Security Design

This section documents the security controls built into the Supplier Trust Engine so that operators and auditors can verify the posture of a running deployment.

### Authentication & Authorization

- All mutating endpoints (`POST /v1/score`, `POST /v1/procure/evaluate`) require a valid `X-API-Key` header.
- The API key is never exposed to the browser. It is injected server-side by the nginx reverse proxy at container startup via `envsubst`, sourced from the `API_KEY` environment variable on the host.
- Read-only endpoints (`GET /v1/suppliers`, `GET /v1/stats`, `GET /health`) are served without a key so that public dashboards remain functional, but they are still covered by rate limiting.

### Transport Security

- All external traffic must be terminated at TLS 1.2+ by the upstream load balancer or nginx before reaching the API container.
- The `docker-compose.yml` provided for local development binds ports to `127.0.0.1` only. Production deployments must sit behind a reverse proxy that enforces HTTPS.

### CORS

- The `Access-Control-Allow-Origin` header is derived from the `ALLOWED_ORIGINS` environment variable (comma-separated list).
- `*` (wildcard) is never used. Deployments default to `http://localhost,http://localhost:80` and must be narrowed to the actual dashboard origin in production.

### Rate Limiting

- All API endpoints are guarded by `slowapi` (a Starlette-compatible `limits` wrapper):
  - GET endpoints: **30 requests / minute** per IP
  - POST endpoints: **10 requests / minute** per IP
- Exceeding the limit returns HTTP 429 with a `Retry-After` header.

### Input Validation

- All request bodies are validated by Pydantic v2 models with explicit `Field` constraints (`min_length`, `max_length`, `ge`, `le`).
- List fields (e.g. `required_certs`, `country_prefer`) are capped to 100 characters per element.
- Numeric bounds (`min_trust_score`, `max_results`) are enforced at the model layer before any DB query is constructed.
- SQL parameters are always passed as positional bind parameters (`?`); no string interpolation is used in queries.

### Error Handling

- A global `@app.exception_handler(Exception)` catches all unhandled errors, logs them server-side via `loguru`, and returns a generic `{"detail": "Internal server error"}` to the caller.
- Stack traces, file paths, and internal model details are never surfaced in API responses.

### Dependency Scanning

- GitHub Dependabot is configured (`.github/dependabot.yml`) to scan both `pip` and `npm` dependency graphs weekly and open automated PRs for vulnerable or outdated packages.
- All Python and Node.js dependencies are pinned to exact versions in `requirements.txt` and `dashboard/package-lock.json` respectively.

### Data Privacy

- The engine stores publicly available trade data (UN Comtrade), publicly listed certification statuses (OEKO-TEX, GOTS), and shipping manifests sourced from public import/export records (ImportYeti).
- No personally identifiable information (PII) is collected or stored.
- The DuckDB file (`data/trust_engine.duckdb`) is excluded from version control via `.gitignore` and must not be committed or pushed to any repository.

---
*Last Updated: April 6, 2026*
