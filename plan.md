# Plan: Business Event Logging with Apache Pinot via Kafka

## Goal
Add operational monitoring for all business events in the warehouse app by publishing structured events to Kafka and ingesting them into Apache Pinot for real-time analytics.

---

## Oracle Cloud Free Tier Constraints

**Current instance**: VM.Standard.A1.Flex — 1 OCPU (ARM), 6GB RAM

| Service | Estimated RAM |
|---------|--------------|
| PostgreSQL (pgvector) | ~300MB |
| FastAPI + Nginx | ~200MB |
| Kafka (KRaft, no ZooKeeper) | ~500MB |
| Pinot (standalone mode) | ~1.5-2GB |
| OS + Docker overhead | ~500MB |
| **Total** | **~3-3.5GB** |

Fits within 6GB with ~2.5GB headroom. To ensure stability:
- **Set `mem_limit`** on every container in docker-compose to prevent OOM kills
- **Kafka must use KRaft mode** (no ZooKeeper) — saves ~500MB
- **Pinot must run standalone** (controller+broker+server in one JVM) — saves ~1-2GB vs separate processes
- **Set JVM heap limits** for Kafka (`KAFKA_HEAP_OPTS: -Xmx256m`) and Pinot (`JAVA_OPTS: -Xms512m -Xmx1g`)
- All Docker images must support **linux/arm64** (both `apache/kafka` and `apachepinot/pinot` do since v0.12+)

**Optional upgrade**: Oracle Free Tier allows up to 4 OCPUs + 24GB across all A1 instances. If 6GB becomes tight, bump to 2 OCPUs / 12GB in Terraform (`deploy/main.tf`) at no cost.

---

## Step 1: Add Kafka + Pinot to Docker Compose

**File**: `docker-compose.yml`

Add three services:
- **kafka** — Single-node Kafka in KRaft mode (no ZooKeeper). Image: `apache/kafka:3.7.0`. Exposes port 9092 internally. `mem_limit: 512m`, `KAFKA_HEAP_OPTS: -Xmx256m`.
- **pinot** — Apache Pinot in standalone mode (controller + broker + server in one). Image: `apachepinot/pinot:1.1.0`. Exposes port 9000 (UI/API). `mem_limit: 1536m`, `JAVA_OPTS: -Xms512m -Xmx1g`.
- **pinot-init** — One-shot container that waits for Pinot to be ready, then creates the table schema via Pinot's REST API.

Add `mem_limit` to existing services too: `db: 512m`, `backend: 512m`, `frontend: 128m`.

Add environment variable `KAFKA_BOOTSTRAP_SERVERS=kafka:9092` to the backend service.

## Step 2: Define Pinot Table Schema

**New file**: `pinot/schema.json`

Create a single Pinot schema `warehouse_events` with these columns:
- `event_id` (STRING, dimension) — UUID
- `event_type` (STRING, dimension) — e.g., `pick_validated`, `order_created`
- `timestamp_ms` (LONG, dateTime) — epoch millis, used as time index
- `user_id` (INT, dimension)
- `username` (STRING, dimension)
- `resource_type` (STRING, dimension) — e.g., `order`, `sku`, `pick`
- `resource_id` (INT, dimension)
- `details` (JSON, dimension) — flexible JSON payload with event-specific data

**New file**: `pinot/table.json`

REALTIME table config pointing to Kafka topic `warehouse_events`, consuming from the earliest offset.

**New file**: `pinot/init.sh`

Script that waits for Pinot to be healthy, then POSTs the schema and table config.

## Step 2b: Enhance Matching Service to Return Top-N Candidates

**File**: `backend/app/services/matching.py`

Currently `find_best_match()` returns only the single best SKU. Enhance it to also return the top-N candidates so events can capture *why* a match was chosen:

1. Add a new function `find_best_matches(db, embedding, top_n=5)` that:
   - Changes `LIMIT 1` → `LIMIT :top_n` in the pgvector query
   - Returns a list of `(SKU, similarity)` tuples for the top-N candidates
   - Still applies the active SKU filter
2. Refactor `find_best_match()` to call `find_best_matches(top_n=1)` internally (no behavior change)
3. Update callers (picks, receiving, vision routers) to use `find_best_matches()` so they have the full candidate list available for event publishing

The top-N candidates will be included in the event `details` payload:
```json
{
  "vision_description": "Red wine box, Château Margaux 2018...",
  "candidates": [
    {"sku_code": "WIN-003", "sku_name": "Margaux 2018", "similarity": 0.92},
    {"sku_code": "WIN-001", "sku_name": "Margaux 2016", "similarity": 0.85},
    {"sku_code": "WIN-012", "sku_name": "Pauillac 2019", "similarity": 0.64}
  ],
  "matched_sku": "WIN-003",
  "threshold": 0.75
}
```

This enables queries in Pinot like:
- "Show picks where the correct SKU was in top 3 but not ranked #1"
- "Average confidence gap between #1 and #2 candidates"
- "Which SKUs are most often confused with each other"

## Step 3: Create Event Publisher Module

**New file**: `backend/app/events.py`

A lightweight module that:
1. Initializes a `confluent_kafka.Producer` connected to `KAFKA_BOOTSTRAP_SERVERS`
2. Exposes a `publish_event(event_type: str, details: dict, user: User | None, resource_type: str, resource_id: int | None)` function
3. Serializes events as JSON with `event_id` (uuid4), `timestamp_ms`, user info, and the details dict
4. Calls `producer.produce()` which is non-blocking (buffered internally)
5. Has a background flush (producer auto-flushes, plus a flush on app shutdown)
6. Gracefully degrades — if Kafka is unavailable, logs a warning and continues (monitoring should never break the app)

**Config change**: `backend/app/config.py` — add `kafka_bootstrap_servers: str = ""` setting.

**Dependency**: Add `confluent-kafka` to `backend/requirements.txt`.

## Step 4: Instrument All Business Operations

Add `publish_event()` calls at the end of each business operation (after DB commit succeeds):

### Orders (`routers/orders.py`)
- `order_created` — order_number, customer_name, line_count
- `order_status_changed` — order_id, old_status, new_status
- `order_deleted` — order_id, order_number

### Picks (`routers/picks.py`)
- `pick_validated` — order_line_id, expected_sku_code, matched_sku_code, confidence, correct, message, vision_description, top-N candidates with similarity scores

### Receiving (`routers/receiving.py`)
- `box_identified` — matched_sku_code, confidence (or null if no match), vision_description, top-N candidates with similarity scores
- `product_created_inline` — sku_code, name

### SKUs (`routers/skus.py`)
- `sku_created` — sku_code, name
- `sku_updated` — sku_code, changed_fields
- `sku_deleted` — sku_code
- `reference_image_uploaded` — sku_code, image_id
- `reference_image_deleted` — sku_code, image_id

### Auth (`routers/auth.py`)
- `user_login` — username, success (true/false)
- `user_created` — username, role
- `user_deleted` — username

### Vision (`routers/vision.py`)
- `vision_identify` — matched_sku_code, confidence (or null), vision_description, top-N candidates with similarity scores

## Step 5: Add Startup/Shutdown Hooks

**File**: `backend/app/main.py`

- On startup: initialize the Kafka producer (call `events.init_producer()`)
- On shutdown: flush and close the producer (call `events.shutdown_producer()`)

## Step 6: Add `KAFKA_BOOTSTRAP_SERVERS` to Deployment Config

**Files**: `docker-compose.yml` (backend env), `.env.example`, `deploy/cloud-init.yaml`

---

## Event Schema Example

```json
{
  "event_id": "a1b2c3d4-...",
  "event_type": "pick_validated",
  "timestamp_ms": 1709568000000,
  "user_id": 3,
  "username": "courier1",
  "resource_type": "pick",
  "resource_id": 42,
  "details": {
    "order_line_id": 42,
    "expected_sku_code": "WIN-001",
    "matched_sku_code": "WIN-003",
    "confidence": 0.87,
    "correct": false
  }
}
```

## Accessing Logs

Pinot's query console (port 9000) is **not exposed publicly**. Access it via SSH tunnel:

```bash
ssh -L 9000:localhost:9000 opc@<your-server-ip>
```

Then open `http://localhost:9000` in your browser to run SQL queries against `warehouse_events`.

No in-app dashboard — Pinot's built-in console is sufficient for now.

---

## Files Changed (Summary)

| File | Change |
|------|--------|
| `docker-compose.yml` | Add kafka, pinot, pinot-init services |
| `backend/requirements.txt` | Add `confluent-kafka` |
| `backend/app/config.py` | Add `kafka_bootstrap_servers` setting |
| `backend/app/services/matching.py` | Add `find_best_matches()` returning top-N candidates |
| `backend/app/events.py` | **New** — Kafka producer + `publish_event()` |
| `backend/app/main.py` | Init/shutdown Kafka producer |
| `backend/app/routers/orders.py` | Add event publishing (3 events) |
| `backend/app/routers/picks.py` | Add event publishing (1 event) |
| `backend/app/routers/receiving.py` | Add event publishing (2 events) |
| `backend/app/routers/skus.py` | Add event publishing (4 events) |
| `backend/app/routers/auth.py` | Add event publishing (3 events) |
| `backend/app/routers/vision.py` | Add event publishing (1 event) |
| `pinot/schema.json` | **New** — Pinot table schema |
| `pinot/table.json` | **New** — Pinot realtime table config |
| `pinot/init.sh` | **New** — One-shot Pinot table creation |
| `.env.example` | Add `KAFKA_BOOTSTRAP_SERVERS` |

---
---

# Plan: Reject Non-Wine Items & Prevent False Positives

## Problem

The pipeline always returns the nearest-neighbor match regardless of whether the
scanned item is actually wine. A shoe box gets embedded, compared via cosine
similarity, and the closest wine reference wins — even at 0.94 confidence.
There is no "this isn't wine" gate.

## Root Cause

`describe_image()` → `generate_embedding()` → `find_best_match()` is a straight
pipeline with only a similarity threshold (0.80). Cosine similarity between
unrelated items in a small reference DB can still be very high because the
vectors don't spread far apart.

## Changes

### 1. Add wine-relevance classification to the vision prompt

**File:** `backend/app/services/embedding.py`

Extend `VISION_PROMPT` to ask the model to start its response with a structured
flag: `WINE_PRODUCT: YES` or `WINE_PRODUCT: NO`.

Add a new function `parse_vision_response(raw: str) -> tuple[bool, str]` that:
- Extracts the `WINE_PRODUCT` flag
- Returns `(is_wine, cleaned_description)` — the description without the flag
  line, so embeddings stay clean

Update `describe_image()` to return `(description, is_wine)`.

### 2. Gate the matching pipeline on `is_wine`

**File:** `backend/app/services/embedding.py`

Update `process_image()` signature to return `(description, embedding, is_wine)`.

**Files:** `backend/app/routers/receiving.py`, `backend/app/routers/vision.py`

Before calling `find_best_match`, check `is_wine`. If `False`:
- Skip embedding search entirely
- Return a clear rejection: `{"match": null, "rejected": true, "reason": "not_wine"}`
- Log the event (Kafka) with `rejection_reason: "not_wine"`

### 3. Add `rejected` / `rejection_reason` to API responses

**File:** `backend/app/schemas.py`

Add optional fields to `IdentifyResponse` / `BookingResponse`:
- `rejected: bool = False`
- `rejection_reason: str | None = None`

### 4. Store rejected scans for audit/review

**File:** `backend/app/models.py`

Add a lightweight `ScanLog` model:
- `id`, `scanned_by`, `scan_image_path`, `vision_description`,
  `is_wine: bool`, `best_similarity: float | None`,
  `matched_sku_id: int | None`, `rejected: bool`, `rejection_reason: str | None`,
  `created_at`

This lets operators inspect what was rejected and why — important for tuning.

Write to `ScanLog` on every scan (successful match, no-match, and rejection).

Generate an Alembic migration for the new table.

### 5. Update frontend to show rejection state

**File:** `frontend/src/components/receive.tsx`

When `rejected === true`:
- Show a distinct red banner: "Dit is geen wijnproduct" (or similar)
- Do NOT show the "Doos niet herkend" generic error — make the distinction clear
- Allow the user to scan again immediately

### 6. Increase default threshold (optional, config-only)

Consider raising `match_threshold` from 0.80 → 0.85 in `config.py`. This is a
secondary defence — the vision gate is the primary one. This can be tuned via
the `MATCH_THRESHOLD` env var without code changes.

## Files Changed

| File | Change |
|------|--------|
| `backend/app/services/embedding.py` | Vision prompt + `parse_vision_response` + updated returns |
| `backend/app/services/matching.py` | No changes needed |
| `backend/app/routers/receiving.py` | Gate on `is_wine`, write `ScanLog` |
| `backend/app/routers/vision.py` | Gate on `is_wine`, write `ScanLog` |
| `backend/app/schemas.py` | Add `rejected`/`rejection_reason` fields |
| `backend/app/models.py` | Add `ScanLog` model |
| `backend/alembic/versions/xxx_add_scan_logs.py` | Migration |
| `frontend/src/components/receive.tsx` | Rejection UI |
| `frontend/src/lib/api.ts` | Handle new response fields |

## Out of Scope

- Re-ranking or multi-stage matching (future improvement)
- Category-specific embedding models
- Human-in-the-loop confirmation for low-confidence matches
