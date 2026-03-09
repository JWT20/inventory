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

| Role     | Permissions                                    |
|----------|------------------------------------------------|
| admin    | Full access: SKUs, orders, users, delete       |
| merchant | Manage SKUs, import orders, upload images       |
| courier  | Scan boxes only                                |

## Workflow

### 1. Import an order (CSV/Excel)
Upload a CSV or Excel file with columns: `producent, wijnnaam, type, jaargang, volume, aantal`.

- Existing SKUs are matched automatically by generated SKU code
- New SKUs are created (without reference image)
- Order starts in **draft** status

### 2. Upload reference images for new SKUs
After import, the system shows which SKUs need a reference image. Upload a photo of each box.

### 3. Activate the order
Once all SKUs have at least one reference image, activate the order. Status becomes **active**.

### 4. Scan boxes
Open an active order and scan boxes with the camera:
- Vision AI identifies the box → matches against order lines
- Each scan books 1 box on the correct line
- Display: **"Zet op rolcontainer [KLANT X]"**
- When all lines are fully scanned → order status becomes **completed**

### SKU structure
Each SKU is identified by wine-specific attributes:

| Field | Example |
|-------|---------|
| Producent | Château Margaux |
| Wijnnaam | Grand Vin |
| Type | Rood / Wit / Rosé / Mousserend |
| Jaargang | 2019 (or empty for NV) |
| Volume | 0.75L |

The `sku_code` and display `name` are auto-generated from these fields.

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
| POST | `/api/orders/import` | Import order from CSV/Excel |
| GET | `/api/orders` | List orders (filter by status) |
| GET | `/api/orders/{id}` | Order detail with lines |
| DELETE | `/api/orders/{id}` | Delete order |
| POST | `/api/orders/{id}/activate` | Activate order |
| POST | `/api/orders/{id}/scan` | Scan box for order |
| POST | `/api/receiving/identify` | Ad-hoc box identification |
| POST | `/api/vision/identify` | Ad-hoc vision identification |

## Event Logging

All business operations are published to Kafka and ingested by Apache Pinot for real-time analytics. Events include logins, SKU changes, order imports, scans (with LLM descriptions and confidence scores), and user management.

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
