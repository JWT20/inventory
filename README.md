# WijnPick - Vision-based Wine Box Identification

Eliminates person-dependency in the wine picking process by identifying boxes via camera, without stickers or barcodes.

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
                                  └─▶ Kafka ──▶ Apache Pinot
                                      (events)  (analytics)
```

- **Frontend**: React + TypeScript mobile webapp with camera scan
- **Backend**: FastAPI (Python 3.12, sync endpoints, no local ML models)
- **Vision**: Google Gemini 2.5-flash for image description
- **Embeddings**: gemini-embedding-001 (3072D) for vector matching
- **Database**: PostgreSQL 16 + pgvector for cosine similarity search
- **Event logging**: Kafka (KRaft) → Apache Pinot (real-time analytics)
- **Hosting**: Docker Compose, designed for Oracle Cloud Always Free (6GB RAM)

## Quick Start

```bash
# 1. Clone and configure
cp .env.example .env
# Edit .env: set GEMINI_API_KEY, POSTGRES_PASSWORD, SECRET_KEY, ADMIN_PASSWORD

# 2. Start all services
docker compose up -d

# 3. Open in browser
# http://localhost:8080

# 4. Default admin login uses the ADMIN_PASSWORD from .env
```

## User Roles

| Role     | Permissions                                       |
|----------|---------------------------------------------------|
| admin    | Full access: SKUs, orders, users, delete          |
| merchant | Manage SKUs, orders, and reference images         |
| courier  | Scan boxes and book received items                |

## Workflow

### Register a new SKU
1. Go to the **Producten** tab
2. Click **+ Nieuw**, fill in wine details (producent, wijnaam, type, jaargang, volume)
3. SKU code is auto-generated from wine fields
4. Upload a reference photo — embedding is generated automatically via Gemini

### Order workflow
1. **Create order**: Upload a CSV or use the manual form (merchant/admin)
2. **Pending images**: If new SKUs lack reference photos, upload them inline
3. **Activate**: Once all SKUs have reference images, activate the order
4. **Scan & Book**: Courier scans boxes, each scan creates a booking (1 scan = 1 box)
5. **Complete**: Order auto-completes when all lines are fully booked

### Identify a box (receiving)
1. Go to the **Scan & Boek** tab
2. Camera opens automatically
3. Point at the box and press **Scan**
4. Result: matched SKU with confidence score, or "not recognized"
5. If not recognized: create the product inline with a reference photo

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check |
| **Auth** | | |
| POST | `/api/auth/login` | Login, returns JWT |
| GET | `/api/auth/me` | Current user info |
| GET/POST | `/api/auth/users` | List / create users (admin) |
| DELETE | `/api/auth/users/{id}` | Delete user (admin) |
| **SKUs** | | |
| GET/POST | `/api/skus` | List / create SKUs |
| GET/PATCH/DELETE | `/api/skus/{id}` | Read / update / delete SKU |
| POST | `/api/skus/{id}/images` | Upload reference image |
| GET | `/api/skus/{id}/images` | List reference images |
| DELETE | `/api/skus/{id}/images/{image_id}` | Delete reference image |
| **Orders** | | |
| GET/POST | `/api/orders` | List / create orders |
| GET/DELETE | `/api/orders/{id}` | Read / delete order |
| POST | `/api/orders/upload-csv` | Create order from CSV |
| POST | `/api/orders/{id}/activate` | Activate order |
| GET | `/api/orders/{id}/bookings` | List bookings for order |
| **Receiving** | | |
| POST | `/api/receiving/identify` | Identify box via camera |
| POST | `/api/receiving/book` | Book identified box (1 scan = 1 box) |
| POST | `/api/receiving/new-product` | Create SKU inline with image |
| **Vision** | | |
| POST | `/api/vision/identify` | Ad-hoc box identification |

## Event Logging

All business operations are published to Kafka and ingested by Apache Pinot for real-time analytics. Events include logins, SKU changes, vision identifications (with LLM descriptions and confidence scores), order management, and bookings.

See [`docs/event-logging.md`](docs/event-logging.md) for query examples.

## Deployment

Designed for Oracle Cloud Always Free tier (1 OCPU ARM, 6GB RAM). See `deploy/` for Terraform provisioning and setup scripts.

```bash
cd deploy
# 1. Configure terraform.tfvars
# 2. Provision VM
./provision.sh
# 3. Deploy application
./setup.sh
```

## Project Structure

```
backend/          FastAPI application
  app/
    routers/      API endpoints (auth, skus, orders, receiving, vision)
    services/     Gemini vision + embedding, pgvector matching
    events.py     Kafka event publisher
    config.py     Settings via pydantic-settings
frontend/         React + Vite + Tailwind + Shadcn/ui
deploy/           Terraform + shell scripts for Oracle Cloud
pinot/            Schema and table config for Apache Pinot
scripts/          Database init (pgvector extension)
nginx/            Reverse proxy configuration
```

## Testing

```bash
cd backend
pytest tests/ -v
```
