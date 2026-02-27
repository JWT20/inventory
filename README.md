# WijnPick - Vision-based Wine Box Identification

Elimineert persoonsafhankelijkheid in het wijn-pickproces door dozen te herkennen via camera, zonder stickers of barcodes.

## Architectuur

```
┌──────────┐     ┌──────────┐     ┌──────────────┐     ┌───────────────┐
│  Telefoon │────▶│  Nginx   │────▶│  FastAPI      │────▶│  PostgreSQL   │
│  (Camera) │◀────│  :80     │◀────│  + CLIP model │◀────│  + pgvector   │
└──────────┘     └──────────┘     └──────────────┘     └───────────────┘
```

- **Frontend**: Mobiele webapp met camera-scan
- **Backend**: FastAPI + OpenCLIP (ViT-B/32) voor image embeddings
- **Database**: PostgreSQL 16 + pgvector voor vector similarity search
- **Hosting**: Docker Compose, geschikt voor Oracle Cloud Always Free

## Quick Start

```bash
# 1. Clone en configureer
cp .env.example .env
# Pas .env aan (minimaal POSTGRES_PASSWORD en SECRET_KEY)

# 2. Start alles
docker compose up -d

# 3. Open in browser
# http://localhost (of je DuckDNS domein)
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

### VM Setup (Always Free Tier)
```bash
# Ampere A1 - 4 OCPU, 24GB RAM
sudo apt update && sudo apt install -y docker.io docker-compose-v2
sudo usermod -aG docker $USER

# Firewall
sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
```

### DuckDNS + HTTPS
Voeg een Caddy reverse proxy toe of gebruik certbot met nginx voor HTTPS via DuckDNS.

## API Endpoints

| Method | Path | Beschrijving |
|--------|------|-------------|
| GET | `/api/health` | Health check |
| GET/POST | `/api/skus` | SKU's beheren |
| POST | `/api/skus/{id}/images` | Referentiebeeld uploaden |
| GET/POST | `/api/orders` | Orders beheren |
| POST | `/api/picks/validate/{line_id}` | Pick valideren met camera |
| POST | `/api/vision/identify` | Losse doos identificeren |
