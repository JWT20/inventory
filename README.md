# WijnPick - Vision-based Wine Box Identification

Elimineert persoonsafhankelijkheid in het wijn-pickproces door dozen te herkennen via camera, zonder stickers of barcodes.

## Architectuur

```
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌───────────────┐
│  Telefoon │────▶│  Nginx   │────▶│  FastAPI  │────▶│  PostgreSQL   │
│  (Camera) │◀────│  :80     │◀────│          │◀────│  + pgvector   │
└──────────┘     └──────────┘     └────┬─────┘     └───────────────┘
                                       │
                                       ▼
                                  ┌──────────┐
                                  │  OpenAI  │
                                  │  Vision  │
                                  │  + Embed │
                                  └──────────┘
```

- **Frontend**: Mobiele webapp met camera-scan
- **Backend**: FastAPI (lightweight, geen PyTorch/ML lokaal)
- **Vision**: OpenAI GPT-4o-mini voor beeldbeschrijving
- **Embeddings**: OpenAI text-embedding-3-small voor vector matching
- **Database**: PostgreSQL 16 + pgvector voor vector similarity search
- **Hosting**: Docker Compose, geschikt voor Oracle Cloud Always Free

## Quick Start

```bash
# 1. Clone en configureer
cp .env.example .env
# Pas .env aan (minimaal OPENAI_API_KEY, POSTGRES_PASSWORD en SECRET_KEY)

# 2. Start alles
docker compose up -d

# 3. Open in browser
# http://localhost (of https://dockscan.nl)
```

## Operationeel Proces

### Nieuwe SKU registreren
1. Ga naar **SKU's** tab
2. Klik **+ Nieuw**, vul SKU code en naam in
3. Na opslaan: maak foto van de doos via **Foto uploaden**
4. Embedding wordt automatisch gegenereerd

### Order picken
1. Ga naar **Scan** tab
2. Selecteer een order
3. Camera opent automatisch
4. Richt op doos en druk **Scan**
5. Resultaat: 🟢 Correct / 🔴 Verkeerd / 🟡 Niet herkend

## Oracle Cloud Deployment

Zie `deploy/` map voor automatische provisioning scripts.

```bash
# 1. Pas deploy/terraform.tfvars aan
# 2. Provision VM
cd deploy && ./provision.sh
# 3. Setup op de VM
./setup.sh
```

## API Endpoints

| Method | Path | Beschrijving |
|--------|------|-------------|
| GET | `/api/health` | Health check |
| GET/POST | `/api/skus` | SKU's beheren |
| POST | `/api/skus/{id}/images` | Referentiebeeld uploaden |
| GET/POST | `/api/orders` | Orders beheren |
| POST | `/api/picks/validate/{line_id}` | Pick valideren met camera |
| POST | `/api/vision/identify` | Losse doos identificeren |
