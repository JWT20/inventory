# WijnPick — Production Readiness Plan

Current state: the app works end-to-end for warehouse receiving and labeling.
It has Docker Compose deployment, role-based auth, CI/CD via GitHub Actions,
and **HTTPS via Caddy + DuckDNS** (automatic TLS certificates).

What's already solid:
- HTTPS termination via Caddy reverse proxy (`deploy/setup.sh`)
- DuckDNS dynamic DNS with cron-based IP updates (`deploy/cloud-init.yaml`)
- Auto-generated secrets (random passwords) during provisioning
- Docker healthchecks with dependency ordering

This plan covers what's still needed, ordered by priority.

---

## Phase 1: Security Hardening (Critical)

### 1.1 Lock down CORS
**File:** `backend/app/main.py:24-30`

The current config allows **all origins** with credentials, which means any website
can make authenticated API calls on behalf of a logged-in user.

```python
# CURRENT (dangerous)
allow_origins=["*"]

# FIX: restrict to your DuckDNS domain
allow_origins=[f"https://{os.environ['DUCKDNS_DOMAIN']}.duckdns.org"]
# or hardcode: allow_origins=["https://yoursubdomain.duckdns.org"]
```

**Effort:** 15 min | **Risk if skipped:** High — enables cross-site request forgery

### 1.2 Add rate limiting to login
**File:** `backend/app/routers/auth.py`

There is no rate limiting on `/api/auth/login`. An attacker can brute-force
credentials indefinitely.

**Action:** Add `slowapi` or a simple in-memory rate limiter (e.g. 5 attempts
per minute per IP).

**Effort:** 1-2 hours | **Risk if skipped:** High — brute-force login attacks

### 1.3 Validate file uploads at the backend level
**Files:** `backend/app/routers/receiving.py`, `backend/app/routers/skus.py`

Currently the backend accepts any `UploadFile` without checking:
- File type (magic bytes, not just extension)
- File size (only Nginx enforces 20MB)
- Image validity (is it actually a decodable image?)

**Action:** Add a utility that validates uploads are JPEG/PNG, under a max size,
and decodable by Pillow before storing.

**Effort:** 2-3 hours | **Risk if skipped:** Medium — malicious file uploads

### 1.4 Tighten JWT token management
**File:** `backend/app/auth.py`

- 90-day token expiry is very long. Reduce to 7-14 days.
- Add a short-lived access token + longer refresh token pattern,
  or at minimum add a `/api/auth/refresh` endpoint.
- Invalidate tokens on password change (requires storing a per-user token
  generation timestamp or version in the DB).

**Effort:** 3-4 hours | **Risk if skipped:** Medium — stolen tokens stay valid for months

### 1.5 Add security headers
**File:** Caddyfile (`deploy/setup.sh`) or `frontend/nginx.conf`

Caddy already handles HTTPS and sets HSTS automatically. Add the remaining
headers either in the Caddyfile or in the Nginx config inside the frontend
container:
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Content-Security-Policy`

**Effort:** 1 hour | **Risk if skipped:** Medium — clickjacking, MIME sniffing

---

## Phase 2: Testing (Critical)

### 2.1 Backend unit tests
There are **zero tests** in the entire codebase. Start with:

1. **Auth tests** — login, token creation/validation, role enforcement
2. **SKU CRUD tests** — create, read, update, delete
3. **Vision/matching tests** — mock OpenAI calls, verify threshold logic
4. **Label generation tests** — barcode generation, ZPL output

**Stack:** pytest + httpx (FastAPI TestClient) + pytest-asyncio.
Use a test database (SQLite or a separate Postgres container).

**Effort:** 2-3 days | **Risk if skipped:** Critical — no safety net for regressions

### 2.2 Frontend tests
Add at minimum:
- Component tests for login flow, SKU management, receiving workflow
- API mock tests to verify error handling

**Stack:** Vitest + React Testing Library.

**Effort:** 1-2 days

### 2.3 CI test gate
**File:** `.github/workflows/deploy.yml`

Currently the pipeline deploys on every push to main with **no validation**.
Add a step before deployment that runs:
1. Backend linting (ruff)
2. Backend tests (pytest)
3. Frontend type-check (tsc --noEmit)
4. Frontend build (vite build)

Only deploy if all checks pass.

**Effort:** 2-3 hours

---

## Phase 3: Code Quality & Consistency

### 3.1 Add linting and formatting
**Backend:** Add `ruff` (linting + formatting in one tool).
Create `pyproject.toml` with ruff config.

**Frontend:** Add `eslint` + `prettier` with configs.

Add a pre-commit hook (via `pre-commit` framework) so these run on every commit.

**Effort:** 2-3 hours

### 3.2 Remove dead code — Order/Pick models
**Files:** `backend/app/routers/orders.py`, `backend/app/routers/picks.py`

These routers reference models (`Order`, `OrderLine`, `PickLog`) and schemas
(`OrderCreate`, `OrderResponse`, `PickResult`) that **do not exist**.
The routes will crash at runtime.

**Decision needed:** Either implement the order/picking feature or remove
the dead routers. Leaving broken code is confusing and a potential crash risk.

**Effort:** 1 hour (remove) or 1-2 days (implement)

### 3.3 Replace deprecated startup event
**File:** `backend/app/main.py:60`

`@app.on_event("startup")` is deprecated in modern FastAPI. Replace with
a `lifespan` context manager.

**Effort:** 30 min

### 3.4 Use Alembic for database migrations
Alembic is in `requirements.txt` but never configured. The current approach
(manual `ALTER TABLE` in Python code + `create_all()`) is fragile and doesn't
track migration history.

**Action:** Initialize Alembic, create an initial migration from current models,
and remove the manual migration code from `main.py`.

**Effort:** 2-3 hours

---

## Phase 4: Observability & Operations

### 4.1 Structured logging
Replace `logging.basicConfig(level=logging.INFO)` with structured JSON logging
(e.g. `structlog` or `python-json-logger`). This makes logs parseable by any
log aggregation tool.

**Effort:** 2-3 hours

### 4.2 Improve health check
**File:** `backend/app/main.py:84-86`

The current health check returns `{"status": "ok"}` without actually testing
the database connection. Add a `SELECT 1` query so the health endpoint reflects
real system health.

**Effort:** 30 min

### 4.3 Add application metrics
Instrument with Prometheus metrics (via `prometheus-fastapi-instrumentator` or
similar):
- Request count/latency per endpoint
- OpenAI API call count/latency/errors
- Vision match success rate and confidence distribution
- Active users

**Effort:** 3-4 hours

### 4.4 Database backups
There is no backup strategy. A volume loss means all SKUs, images, and
embeddings are gone.

**Action:**
- Add a cron job for `pg_dump` to an object storage bucket (e.g. OCI Object Storage)
- Also back up the uploads volume (reference images)
- Test restore process

**Effort:** 3-4 hours

### 4.5 Error tracking
Integrate Sentry (free tier) or similar for automatic exception reporting
with context. This is much more reliable than grepping container logs.

**Effort:** 1-2 hours

---

## Phase 5: Reliability & Performance

### 5.1 Add request validation and error boundaries
- Add a global exception handler so unexpected errors return clean 500
  responses instead of raw tracebacks
- Add request ID to all log entries for traceability
- Add timeouts on OpenAI API calls (currently no timeout configured)

**Effort:** 2-3 hours

### 5.2 Graceful degradation for OpenAI
If the OpenAI API is down or slow, the entire app becomes unusable.
Add:
- Configurable timeouts on OpenAI calls
- Circuit breaker pattern or fallback (e.g. return "service temporarily
  unavailable" instead of hanging)
- Retry with backoff for transient failures

**Effort:** 3-4 hours

### 5.3 Database connection pooling
The current setup uses SQLAlchemy's default connection pool. For production:
- Configure pool size, max overflow, and pool recycle explicitly
- Add connection health checks

**Effort:** 1 hour

### 5.4 Image optimization
Reference images are stored as-is. Large images waste storage and slow down
uploads. Add server-side resizing (e.g. max 1024px on longest side) before
storing.

**Effort:** 1-2 hours

---

## Phase 6: Deployment & Infrastructure

### 6.1 Add rollback capability
The current CI/CD has no rollback. If a broken deploy goes out, the only
option is to push a fix.

**Action:** Tag Docker images with git SHA, keep last N images, add a
`rollback.sh` script that restarts with a previous image.

**Effort:** 3-4 hours

### 6.2 Zero-downtime deploys
`docker compose up -d` restarts containers, causing brief downtime.
Switch to a blue-green or rolling strategy:
- Build new images, start new containers, health check, swap Nginx upstream,
  stop old containers.

**Effort:** 4-6 hours

### 6.3 Secrets management
Passwords are in `.env` files and plaintext in `cloud-init.yaml`.
Move secrets to a proper secrets manager (OCI Vault, or at minimum
Docker secrets).

**Effort:** 3-4 hours

### 6.4 Add staging environment
Currently there's only production. Add a staging environment that mirrors
production for testing deployments before they go live.

**Effort:** 4-6 hours

---

## Summary — Suggested Order of Implementation

| # | Item | Effort | Impact |
|---|------|--------|--------|
| 1 | Lock down CORS | 15 min | Blocks CSRF attacks |
| 2 | Rate limit login | 1-2 hrs | Blocks brute force |
| 3 | Add backend tests + CI gate | 2-3 days | Safety net for everything else |
| 4 | Validate file uploads | 2-3 hrs | Prevents malicious uploads |
| 5 | Add linting (ruff + eslint) | 2-3 hrs | Code quality baseline |
| 6 | Remove or fix dead Order/Pick code | 1 hr+ | Eliminates runtime crashes |
| 7 | Improve health check | 30 min | Accurate monitoring |
| 8 | Database backups | 3-4 hrs | Disaster recovery |
| 9 | Structured logging | 2-3 hrs | Debuggability |
| 10 | Tighten JWT tokens | 3-4 hrs | Reduces token theft window |
| 11 | Security headers | 1 hr | Defense in depth |
| 12 | Alembic migrations | 2-3 hrs | Safe schema changes |
| 13 | OpenAI graceful degradation | 3-4 hrs | Resilience |
| 14 | Error tracking (Sentry) | 1-2 hrs | Proactive issue detection |
| 15 | Rollback capability | 3-4 hrs | Safe deployments |
| 16 | Staging environment | 4-6 hrs | Test before prod |

**Total estimated effort: ~3-4 weeks for one developer.**

Items 1-2 should be done immediately (same day). Items 3-6 within the first
week. The rest can be prioritized based on operational needs.
