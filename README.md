# WijnPick - Vision-based Wine Box Identification

Eliminates person-dependency in the wine picking process by identifying boxes via camera, without stickers or barcodes. A scanned wine box is matched against reference images using AI vision descriptions and vector similarity search.

## Architecture

```
                                    ┌───────────────┐
                                    │  PostgreSQL   │
┌──────────┐     ┌──────────┐     ┌─┤  + pgvector   │
│  Phone   │────▶│  Nginx   │────▶│ └───────────────┘
│  (Camera)│◀────│  :80     │◀────│
└──────────┘     └──────────┘     │  FastAPI
                                  │
                                  ├─▶ Gemini Vision
                                  │   + Embeddings
                                  │
                                  ├─▶ Langfuse (LLM tracing
                                  │    + prompt management)
                                  │
                                  └─▶ Kafka ──▶ Apache Pinot
                                      (events)  (analytics)
```

| Component | Technology |
|-----------|------------|
| **Frontend** | React 19 + TypeScript + Vite + Tailwind CSS + Shadcn/ui |
| **Backend** | FastAPI (Python 3.12) |
| **Vision** | Google Gemini 2.5-flash for image description |
| **Embeddings** | gemini-embedding-001 (3072-dimensional vectors) |
| **Database** | PostgreSQL 16 + pgvector for cosine similarity search |
| **Event logging** | Kafka (KRaft mode) → Apache Pinot (real-time analytics) |
| **LLM observability** | Langfuse (prompt management + tracing, optional) |
| **Reverse proxy** | Nginx |
| **Hosting** | Docker Compose, designed for Oracle Cloud Always Free (1 OCPU ARM, 6GB RAM) |

## How It Works

1. **Reference registration** — Upload a photo of each wine box. Gemini Vision generates a structured text description, which is then converted to a 3072-dimensional embedding and stored in pgvector.
2. **Scanning** — Point the phone camera at an incoming box. The same vision+embedding pipeline runs on the scan image.
3. **Matching** — PostgreSQL's pgvector performs cosine similarity search between the scan embedding and all reference embeddings. If the best match exceeds the confidence threshold (default 0.80), the box is identified.
4. **Booking** — Each successful scan creates a booking (1 scan = 1 box = 1 booking), assigns the box to a customer rolcontainer, and decrements the remaining count on the order line. Orders auto-complete when all lines are fully booked.

## Quick Start

```bash
# 1. Clone and configure
git clone <repo-url>
cd inventory
cp .env.example .env
# Edit .env: set GEMINI_API_KEY, POSTGRES_PASSWORD, SECRET_KEY, ADMIN_PASSWORD

# 2. Start all services
docker compose up -d

# 3. Open in browser
# http://localhost:8080

# 4. Login with username "admin" and the ADMIN_PASSWORD from .env
```

### Prerequisites

- Docker and Docker Compose
- A Google Gemini API key (for vision and embedding)

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `POSTGRES_USER` | PostgreSQL username | `wijnpick` |
| `POSTGRES_PASSWORD` | PostgreSQL password | — (required) |
| `POSTGRES_DB` | PostgreSQL database name | `wijnpick` |
| `DATABASE_URL` | Full PostgreSQL connection string | built from above |
| `GEMINI_API_KEY` | Google Gemini API key | — (required) |
| `GEMINI_VISION_MODEL` | Vision model for image description | `gemini-2.5-flash` |
| `GEMINI_EMBEDDING_MODEL` | Embedding model for vector generation | `gemini-embedding-001` |
| `SECRET_KEY` | JWT signing key | — (required) |
| `MATCH_THRESHOLD` | Minimum cosine similarity for a match | `0.80` |
| `UPLOAD_DIR` | Directory for uploaded images | `/app/uploads` |
| `ADMIN_PASSWORD` | Password for the auto-created admin account | — (required) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | JWT access token expiration | `30` |
| `REFRESH_TOKEN_EXPIRE_DAYS` | Refresh token expiration | `7` |
| `KAFKA_BOOTSTRAP_SERVERS` | Kafka broker address (optional) | `kafka:9092` |
| `LANGFUSE_PUBLIC_KEY` | Langfuse public key (optional) | `` |
| `LANGFUSE_SECRET_KEY` | Langfuse secret key (optional) | `` |
| `LANGFUSE_HOST` | Langfuse host URL | `https://cloud.langfuse.com` |
| `DOMAIN` | Production domain (used for CORS) | `` (empty = dev mode) |

## User Roles

| Role | Permissions |
|----------|----------------------------------------------|
| admin | Full access: manage SKUs, orders, users, delete anything |
| merchant | Manage own SKUs, orders, and reference images |
| courier | Scan boxes and book received items only |

## Workflow

### 1. Register products (Producten tab)
1. Click **+ Nieuw**, fill in wine details (producent, wijnaam, type, volume)
2. SKU code is auto-generated from wine fields (e.g. `CHAT-GRAN-ROO-750`)
3. Upload one or more reference photos — each photo is processed through Gemini Vision → embedding pipeline

### 2. Create an order (Orders tab)
1. **CSV upload**: Upload a semicolon-delimited CSV with columns: `klant;producent;wijnaam;type;volume;aantal`
2. **Manual form**: Create an order with inline wine details per line
3. The system auto-matches or creates SKUs and sets the order status:
   - `draft` — all SKUs already have reference images
   - `pending_images` — one or more SKUs still need reference photos

### 3. Activate the order
- Once all SKUs in the order have reference images, activate the order (status → `active`)
- Only active orders appear in the scanner workflow

### 4. Scan & book (Scan & Boek tab)
1. Select an active order from the dropdown
2. Camera opens — point at the box and press **Scan**
3. Result: matched SKU with confidence score, or "not recognized"
4. On match: a booking is created, the box is assigned to a customer rolcontainer (`KLANT <customer_name>`)
5. When all lines are fully booked, the order auto-completes

### 5. Ad-hoc identification
- Use the `/api/vision/identify` endpoint or receiving identify to check a box without order context

## API Endpoints

### Health
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check |

### Auth
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/auth/login` | Public | Login, returns JWT + refresh token |
| POST | `/api/auth/refresh` | Public | Exchange refresh token for new access token |
| GET | `/api/auth/me` | Any | Current user info |
| GET | `/api/auth/users` | Admin | List all users |
| POST | `/api/auth/users` | Admin | Create a new user |
| DELETE | `/api/auth/users/{id}` | Admin | Delete a user (cannot delete self) |

### SKUs (Products)
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/skus` | Any | List SKUs (optional `?active_only=true`) |
| POST | `/api/skus` | Merchant/Admin | Create a new SKU |
| GET | `/api/skus/{id}` | Any | Get single SKU |
| PATCH | `/api/skus/{id}` | Merchant/Admin | Update SKU fields |
| DELETE | `/api/skus/{id}` | Admin | Delete a SKU |
| POST | `/api/skus/{id}/images` | Merchant/Admin | Upload reference image (triggers vision + embedding) |
| GET | `/api/skus/{id}/images` | Any | List reference images |
| DELETE | `/api/skus/{id}/images/{image_id}` | Merchant/Admin | Delete a reference image |

### Orders
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/orders/upload-csv` | Merchant/Admin | Create order from CSV file |
| POST | `/api/orders` | Merchant/Admin | Create order manually |
| GET | `/api/orders` | Any | List orders (merchants see own, admins see all) |
| GET | `/api/orders/{id}` | Any | Get order with lines |
| POST | `/api/orders/{id}/activate` | Merchant/Admin | Activate order (requires all SKUs to have images) |
| DELETE | `/api/orders/{id}` | Admin | Delete order and all its bookings |
| GET | `/api/orders/{id}/bookings` | Any | List bookings for an order |

### Customers
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/customers` | Merchant/Admin | List customers |
| POST | `/api/customers` | Merchant/Admin | Create a customer |
| DELETE | `/api/customers/{id}` | Merchant/Admin | Delete a customer |

### Receiving
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/receiving/identify` | Any | Scan a box and identify it (returns top matches or null) |
| POST | `/api/receiving/book` | Any | Scan, identify, and book a box to an order line |
| POST | `/api/receiving/book/confirm` | Any | Confirm a low-confidence match (human approval gate) |
| POST | `/api/receiving/new-product` | Any | Quick-create a SKU with a reference image inline |

### Vision
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/vision/identify` | Any | Ad-hoc box identification (no order context) |

## CSV Format

Semicolon-delimited, UTF-8 (BOM supported). Example:

```csv
klant;producent;wijnaam;type;volume;aantal
Bakker;Château Margaux;Grand Vin;Rood;750;6
De Vries;Domaine Leflaive;Puligny-Montrachet;Wit;750;12
```

Rows with the same SKU code and customer are deduplicated and quantities are summed.

## Event Logging

All business operations publish events to Kafka topic `warehouse_events`, which Apache Pinot ingests for real-time analytics.

**Event types:** `user_login`, `user_created`, `user_deleted`, `sku_created`, `sku_updated`, `sku_deleted`, `reference_image_uploaded`, `reference_image_deleted`, `order_created_from_csv`, `order_created_manual`, `order_activated`, `order_deleted`, `box_identified`, `box_booked`, `product_created_inline`, `vision_identify`

**Event schema:**
```json
{
  "event_id": "uuid",
  "event_type": "box_booked",
  "timestamp_ms": 1710000000000,
  "user_id": 1,
  "username": "admin",
  "resource_type": "booking",
  "resource_id": 42,
  "details": { ... }
}
```

Kafka is optional — if `KAFKA_BOOTSTRAP_SERVERS` is empty or Kafka is unavailable, events are silently skipped and the application continues normally.

See [`docs/event-logging.md`](docs/event-logging.md) for Pinot query examples.

## LLM Observability (Langfuse)

All Gemini Vision and embedding calls are traced via [Langfuse](https://langfuse.com) for observability and prompt management.

**What it provides:**
- Full trace of every scan: prompt input, model output, latency, cost
- Quality scores per trace (`match_confidence`, `match_accepted`)
- **Prompt Management** — edit and version prompts from the Langfuse dashboard without redeploying

**Managed prompts:**
| Langfuse Prompt Name | Used By | Purpose |
|---------------------|---------|---------|
| `classify-and-describe` | Main scan pipeline | Classify image as package + generate description |
| `describe-package` | Reference image upload (skip classification) | Generate description only |

Prompts are fetched from Langfuse on each request. If Langfuse is unavailable or not configured, hardcoded fallback prompts are used.

**Setup:** Set `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_HOST` in `.env`. Create the two prompts above in the Langfuse dashboard. Langfuse is optional — if keys are not set, tracing is silently disabled.

## Project Structure

```
backend/                FastAPI application
  app/
    main.py             App init, migrations, lifespan hooks
    config.py           Settings via pydantic-settings
    auth.py             Password hashing, JWT, auth dependencies
    database.py         SQLAlchemy engine, session factory
    models.py           ORM models (User, SKU, ReferenceImage, Order, OrderLine, Booking)
    schemas.py          Pydantic request/response schemas, SKU code generation
    events.py           Kafka event publisher (graceful degradation)
    routers/
      auth.py           Login, user CRUD
      skus.py           SKU CRUD, reference image upload
      orders.py         CSV upload, manual orders, activation, bookings
      receiving.py      Box identification, booking, inline product creation
      vision.py         Ad-hoc vision identification
    services/
      embedding.py      Gemini Vision + embedding generation
      matching.py       pgvector cosine similarity search
      langfuse_client.py  LLM observability + prompt management (graceful degradation)
  tests/                Pytest suite (SQLite in-memory, mocked Vision API)
    conftest.py         Shared fixtures (test DB, users, tokens)
    test_auth.py        Auth endpoint tests
    test_skus.py        SKU CRUD and code generation tests
    test_orders.py      CSV parsing, order lifecycle tests
    test_matching.py    Embedding and matching logic tests
    test_wine_filter.py Wine-specific filtering tests
    test_langfuse.py    Langfuse graceful degradation tests
frontend/               React + Vite + Tailwind + Shadcn/ui
  src/
    App.tsx             Main app shell with role-based tab navigation
    lib/
      api.ts            HTTP client with auth header injection
      auth.tsx          Auth context provider (React Context)
    components/
      login.tsx         Login form
      receive.tsx       Scanner UI (order select → scan → booking result)
      orders.tsx        Order management (CSV upload, manual create, activate)
      skus.tsx          Product management (CRUD, reference image upload)
      customers.tsx     Customer management
      accounts.tsx      User management (admin only)
      ui/               Shadcn/ui components (button, card, dialog, etc.)
deploy/                 Terraform + shell scripts for Oracle Cloud
  main.tf               VM provisioning
  provision.sh          VM setup
  setup.sh              Application deployment
  redeploy.sh           Redeployment after code changes
  backup-db.sh          Database backup
pinot/                  Apache Pinot configuration
  schema.json           Event schema definition
  table.json            REALTIME table config (Kafka consumer)
  init.sh               One-shot schema/table creation
scripts/
  init-db.sql           PostgreSQL pgvector extension init
nginx/                  Reverse proxy configuration
docs/
  event-logging.md      Event logging docs with Pinot query examples
```

## Database Models

```
User ──< Order ──< OrderLine >── SKU ──< ReferenceImage
                   OrderLine >── Customer
                   OrderLine ──< Booking
                   Order ──< Booking
                   User ──< Booking (scanned_by)
```

**Order statuses:** `draft` → `pending_images` → `active` → `completed` (or `cancelled`)

## Deployment

Designed for Oracle Cloud Always Free tier (1 OCPU ARM, 6GB RAM).

**Memory budget:**
| Service | Limit |
|---------|-------|
| PostgreSQL | 512 MB |
| Kafka (KRaft) | 512 MB |
| Apache Pinot | 1536 MB |
| Backend (FastAPI) | 512 MB |
| Frontend (Nginx) | 128 MB |

```bash
cd deploy
# 1. Generate terraform.tfvars
./generate-tfvars.sh
# 2. Provision VM
./provision.sh
# 3. Deploy application
./setup.sh
# 4. Redeploy after changes
./redeploy.sh
```

## Testing

```bash
cd backend
pip install -r requirements.txt
pytest tests/ -v
```

Tests use SQLite in-memory with a pgvector type compiler shim. No PostgreSQL or Gemini API key needed for tests.

## License

Proprietary — all rights reserved.
