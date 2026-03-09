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
- **Backend**: FastAPI (Python 3.12, no local ML models)
- **Vision**: Google Gemini 2.5-flash for image description
- **Embeddings**: gemini-embedding-001 (3072D) for vector matching
- **Database**: PostgreSQL 16 + pgvector for cosine similarity search
- **Event logging**: Kafka (KRaft) → Apache Pinot (real-time analytics)
- **Labels**: Code128 barcode (PNG), printable PDF, Zebra ZPL
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

| Role     | Permissions                            |
|----------|----------------------------------------|
| admin    | Full access: SKUs, users, delete       |
| merchant | Manage SKUs and reference images       |
| courier  | Scan boxes only                        |

## Workflow

### Register a new SKU
1. Go to the **SKU's** tab
2. Click **+ Nieuw**, fill in SKU code and name
3. After saving: upload a photo of the box via **Foto toevoegen**
4. Embedding is generated automatically via Gemini

### Identify a box (receiving)
1. Go to the **Ontvangst** tab
2. Camera opens automatically
3. Point at the box and press **Scan**
4. Result: matched SKU with confidence score, or "not recognized"
5. If not recognized: create the product inline with a reference photo

### Generate labels
After identifying a box, generate:
- **Barcode PNG** (Code128)
- **Printable PDF** (4x2 inch label)
- **ZPL** (for Zebra thermal printers)

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check |
| POST | `/api/auth/login` | Login, returns JWT |
| GET | `/api/auth/me` | Current user info |
| GET/POST | `/api/auth/users` | User management (admin) |
| GET/POST | `/api/skus` | List / create SKUs |
| GET/PATCH/DELETE | `/api/skus/{id}` | Read / update / delete SKU |
| POST | `/api/skus/{id}/images` | Upload reference image |
| POST | `/api/receiving/identify` | Identify box via camera |
| POST | `/api/receiving/new-product` | Create SKU inline with image |
| POST | `/api/vision/identify` | Ad-hoc box identification |
| GET | `/api/labels/{sku_id}/barcode.png` | Barcode image |
| GET | `/api/labels/{sku_id}/label.pdf` | Printable label |
| GET | `/api/labels/{sku_id}/label.zpl` | Zebra ZPL label |

## Event Logging

All business operations are published to Kafka and ingested by Apache Pinot for real-time analytics. Events include logins, SKU changes, vision identifications (with LLM descriptions and confidence scores), and user management.

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
    routers/      API endpoints (auth, skus, receiving, vision, labels)
    services/     Gemini vision + embedding, pgvector matching
    events.py     Kafka event publisher
frontend/         React + Vite + Tailwind + Shadcn/ui
deploy/           Terraform + shell scripts for Oracle Cloud
pinot/            Schema and table config for Apache Pinot
scripts/          Database init (pgvector extension)
```

## Testing

```bash
cd backend
pytest tests/ -v
```
