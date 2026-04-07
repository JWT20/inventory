# Architecture Review: WijnPick Inventory System

**Date:** 2026-04-07
**Overall Score: 7.8 / 10**

A well-structured, production-ready inventory system with strong domain modeling, thoughtful graceful degradation patterns, and good separation of concerns. The codebase shows maturity in several areas but has room for improvement in testing, frontend architecture, and some backend patterns.

---

## Category Scores

| Category | Score | Notes |
|----------|-------|-------|
| Project Structure & Organization | 8.5/10 | Clean monorepo, domain-scoped routers, services layer |
| Database Design & Models | 8.5/10 | Well-normalized, proper constraints, pgvector integration |
| API Design | 8.0/10 | Consistent REST, confirmation tokens, CSV upload |
| Authentication & Security | 8.5/10 | JWT + refresh rotation, rate limiting, path traversal protection |
| External Service Integration | 9.0/10 | Graceful degradation everywhere, retry logic, concurrency limits |
| Frontend Architecture | 7.0/10 | Simple but missing routing, state management, types |
| Testing | 6.5/10 | Clever test setup but missing critical path coverage |
| Infrastructure & Deployment | 8.0/10 | Docker Compose, CI/CD, auto-migrations, health checks |
| Code Quality & Patterns | 7.5/10 | Consistent style, good logging, some coupling issues |
| Documentation | 8.5/10 | Comprehensive README, API reference, deployment docs |

---

## Detailed Findings

### 1. Project Structure & Organization — 8.5/10

**Strengths:**
- Clean monorepo with clear `backend/`, `frontend/`, `deploy/`, `pinot/` separation
- Backend follows standard FastAPI layout: `models.py`, `schemas.py`, `routers/`, `services/`
- Services layer (`embedding.py`, `matching.py`, `storage.py`, `langfuse_client.py`) cleanly separates external integrations from business logic
- Routers are domain-scoped (`orders.py`, `skus.py`, `receiving.py`, `inventory.py`)

**Concerns:**
- `routers/inventory.py` at 1,043 lines handles shipments, stock movements, inventory overview, pricing, and customer discounts — should split into `shipments.py` and `pricing.py`
- Cross-router imports exist (`receiving.py` imports from `skus.py` and `inventory.py`) — shared logic should move to a service layer

### 2. Database Design & Models — 8.5/10

**Strengths:**
- Well-normalized schema: `Organization → User → Order → OrderLine → Booking`
- SQLAlchemy 2.0 `Mapped[]` type annotations throughout
- Good constraints: `UniqueConstraint` on `(organization_id, name)` for suppliers, conditional unique index on `SupplierSKUMapping`
- Clean pgvector integration (3072-dim embeddings)
- 12 Alembic migrations showing iterative schema evolution

**Concerns:**
- `Organization.enabled_modules` stored as JSON text — should use PostgreSQL `JSONB`
- `SKU.default_price` uses `Numeric(10, 2)` but Python type is `float` — should be `Decimal` to avoid precision issues
- Status fields use plain strings — consider Python `Enum` types

### 3. API Design — 8.0/10

**Strengths:**
- Consistent RESTful patterns across all routers
- Confirmation token pattern for low-confidence bookings (human-in-the-loop gate)
- Health endpoint with proper 503 on DB failure

**Concerns:**
- No API versioning
- No pagination on list endpoints — will break at scale
- No OpenAPI response models on most endpoints
- Mixed `Form()` and JSON bodies across similar endpoints

### 4. Authentication & Security — 8.5/10

**Strengths:**
- JWT access tokens via FastAPI-Users with custom refresh token rotation + revocation
- Rate limiting with Redis/in-memory backends (5 attempts, 5-min lockout)
- Password strength validation
- Path traversal protection on file serving
- Configuration validation fails loudly if secrets are left at defaults

**Concerns:**
- `_revoked_tokens` in-memory dict grows unbounded without Redis
- `require_admin` and `require_platform_admin` are identical functions
- CORS allows `http://localhost:5173` even in production

### 5. External Service Integration — 9.0/10

**Strengths:**
- Graceful degradation on all optional services (Kafka, Langfuse, Redis, Sentry)
- Gemini API: retry with backoff on 429s, `asyncio.Semaphore` for concurrency limiting
- Storage abstraction: `LocalStorage` / `S3Storage` switchable via env var
- Langfuse prompt management with hardcoded fallbacks
- Non-blocking Kafka event publishing with producer reset on failure

**Concerns:**
- Gemini client is a module-global singleton — not easily testable

### 6. Frontend Architecture — 7.0/10

**Strengths:**
- Centralized `api.ts` client with token refresh deduplication
- React Context for auth, role-based tab visibility
- Shadcn/ui for consistent styling

**Concerns:**
- No routing library — browser back/forward doesn't work, no deep linking
- No state management — each page fetches independently
- Large components (receive.tsx: 931 lines, accounts.tsx: 873 lines) mix data fetching, logic, and UI
- No TypeScript interfaces for API response types
- No error boundary components

### 7. Testing — 6.5/10

**Strengths:**
- SQLite in-memory with pgvector type shim — tests run without PostgreSQL
- `_AsyncSessionWrapper` for FastAPI-Users compatibility
- Good fixture hierarchy for all user roles
- Coverage: auth, SKUs, orders, matching, wine filtering, langfuse, vision

**Concerns:**
- No frontend tests (no Vitest/Playwright)
- Missing tests for receiving/booking (core business flow), inventory, customers
- ~2,633 test LOC for ~7,000 backend LOC (~38%)
- CI only runs backend tests — no linting, type checking, or frontend build

### 8. Infrastructure & Deployment — 8.0/10

**Strengths:**
- Docker Compose with health checks, memory limits, dependency ordering
- Terraform for Oracle Cloud + shell provisioning scripts
- GitHub Actions CI/CD: test → rsync → docker rebuild
- Auto-running Alembic migrations on startup with baseline detection
- Scan cleanup job prevents disk bloat

**Concerns:**
- `docker compose build --no-cache` makes deploys slow
- No staging environment
- No scheduled database backups (script exists but isn't automated)

### 9. Code Quality — 7.5/10

**Strengths:**
- Consistent style, good logging with `[TIMING]` patterns
- Pydantic config validation with helpful error messages
- Clean event publishing API

**Concerns:**
- Business logic in routers instead of service layer (CSV parsing, SKU code generation, booking logic)
- `schemas.py` (634 lines) mixes Pydantic models with utility functions
- Some manual response construction instead of Pydantic response models

---

## Top Issues by Priority

| Priority | Issue | Impact |
|----------|-------|--------|
| **High** | No pagination on list endpoints | Will break at scale |
| **High** | No frontend routing | Poor UX, no deep linking |
| **High** | Missing tests for receiving/booking flow | Core logic untested |
| **Medium** | `routers/inventory.py` too large (1,043 lines) | Hard to maintain |
| **Medium** | Cross-router imports | Tight coupling |
| **Medium** | No frontend TypeScript API types | Type safety gap |
| **Medium** | `float` for monetary values | Precision errors |
| **Medium** | CI missing lint/type-check/frontend steps | Quality gaps |
| **Low** | Duplicate admin guards | Dead code |
| **Low** | CORS allows localhost in production | Minor security |

---

## Verdict

A **solid, well-architected production application** for its scope. The graceful degradation patterns, security implementation, and domain modeling are notably well done. Main improvement areas: frontend maturity (routing, state management, types), test coverage for critical business paths, and splitting larger router files. For a resource-constrained deployment target, the architecture choices are pragmatic and appropriate.
