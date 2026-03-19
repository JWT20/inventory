# Implementation Plan: HTTPS, Sentry, Uptime Monitoring, SSH Restriction

## 1. HTTPS with Let's Encrypt (Certbot)

### Current state
- The `frontend` container serves Nginx on port 8080→80 (HTTP only)
- `nginx/nginx.conf` and `frontend/nginx.conf` are identical — both listen on port 80
- The frontend container acts as the single entrypoint (reverse proxy to backend + static files)
- Terraform already opens ports 80 and 443
- `cloud-init.yaml` opens iptables for both 80 and 443
- Domain is configured via `$DOMAIN` env var (currently `dockscan.nl`)

### Architecture decision
**Use a separate Nginx reverse-proxy container + Certbot sidecar**, rather than bolting SSL onto the frontend container. Reasons:
- Keeps the frontend container simple (just static files)
- Standard pattern: dedicated reverse proxy handles TLS termination
- Certbot renewal can run independently

However — looking at the current setup, the frontend container already IS the reverse proxy (it proxies `/api/` to backend). There is also an `nginx/nginx.conf` that's identical but unused in docker-compose. So the cleanest approach is:

**Restructure into: Nginx reverse proxy container (TLS + proxy) → frontend static files served directly, API proxied to backend.**

### Files to create/modify

#### A. Create `certbot/` directory
- No files needed at build time — Certbot runs as a standard Docker image

#### B. Modify `docker-compose.yml`
- **Add `nginx` service** (the TLS-terminating reverse proxy):
  - Image: `nginx:alpine`
  - Ports: `80:80`, `443:443`
  - Volumes:
    - `./nginx/nginx.conf:/etc/nginx/conf.d/default.conf:ro`
    - `certbot-webroot:/var/www/certbot:ro` (for ACME challenge)
    - `certbot-certs:/etc/letsencrypt:ro` (for certificates)
    - `uploads:/app/uploads:ro` (for serving upload files)
  - Depends on: `frontend`, `backend`

- **Add `certbot` service**:
  - Image: `certbot/certbot`
  - Volumes:
    - `certbot-webroot:/var/www/certbot`
    - `certbot-certs:/etc/letsencrypt`
  - Entrypoint: `certonly --webroot -w /var/www/certbot --email admin@${DOMAIN} -d ${DOMAIN} --agree-tos --non-interactive`
  - Restart: `no` (run once to obtain cert)

- **Modify `frontend` service**:
  - Remove `ports: ["8080:80"]` (no longer directly exposed)
  - Keep internal only (nginx proxy will reach it via Docker network)

- **Add volumes**: `certbot-webroot`, `certbot-certs`

#### C. Rewrite `nginx/nginx.conf` — TLS reverse proxy
```nginx
# HTTP → HTTPS redirect + ACME challenge
server {
    listen 80;
    server_name ${DOMAIN};

    # Let's Encrypt ACME challenge
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    # Redirect everything else to HTTPS
    location / {
        return 301 https://$host$request_uri;
    }
}

# HTTPS server
server {
    listen 443 ssl;
    server_name ${DOMAIN};

    ssl_certificate /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;

    # Modern SSL config
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    # Security headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;

    client_max_body_size 20M;

    # Frontend (proxy to frontend container)
    location / {
        proxy_pass http://frontend:80;
        proxy_set_header Host $host;
    }

    # API proxy
    location /api/ {
        proxy_pass http://backend:8000/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 120s;
    }

    # Uploaded images
    location /api/uploads/ {
        alias /app/uploads/;
    }
}
```

**Note:** Since Nginx doesn't natively support env vars in config, we'll use `envsubst` in the container command or a template approach. The simplest method: use an `nginx.conf.template` and generate the config via entrypoint.

#### D. Modify `frontend/nginx.conf` — simplify to static-only
Remove the `/api/` proxy and `/api/uploads/` alias from the frontend's Nginx config, since those are now handled by the outer reverse proxy. The frontend container just serves static files:
```nginx
server {
    listen 80;
    server_name _;
    location / {
        root /usr/share/nginx/html;
        index index.html;
        try_files $uri $uri/ /index.html;
    }
}
```

#### E. Create `scripts/renew-certs.sh`
A script for cert renewal, to be called via cron or a Docker restart:
```bash
#!/bin/bash
docker compose run --rm certbot renew
docker compose exec nginx nginx -s reload
```

#### F. Modify `deploy/cloud-init.yaml`
- Add a cron job for cert renewal (e.g., daily at 3 AM)
- On first boot, run initial cert acquisition before starting the full stack
- Boot sequence: start nginx on HTTP-only first → run certbot → restart nginx with SSL

#### G. Initial certificate bootstrapping challenge
The SSL config references cert files that don't exist on first deploy. Solution:
- Use a two-phase nginx config: start with HTTP-only, run certbot, then switch to full config
- OR: use a startup script that checks if certs exist and falls back to HTTP-only mode
- Recommended: create `scripts/init-ssl.sh` that handles first-time setup

### Testing approach
- Test locally with `docker compose up` (HTTP-only mode works without certs)
- On server: verify ACME challenge works, cert is obtained, HTTPS serves correctly
- Verify HTTP→HTTPS redirect works
- Verify API proxy works over HTTPS
- Check SSL rating at ssllabs.com

---

## 2. Sentry Error Tracking

### Current state
- No error tracking exists
- Backend is FastAPI (Python), frontend is React (TypeScript)
- Langfuse is already integrated as an optional service (good pattern to follow)

### Files to modify

#### A. Backend — `backend/requirements.txt`
Add:
```
sentry-sdk[fastapi]>=2.0
```

#### B. Backend — `backend/app/config.py`
Add setting:
```python
sentry_dsn: str = ""  # leave empty to disable
```

#### C. Backend — `backend/app/main.py`
Add Sentry initialization at app startup (before FastAPI app creation):
```python
import sentry_sdk

if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        traces_sample_rate=0.1,  # 10% of requests for performance monitoring
        environment="production",
    )
```

The FastAPI integration is automatic when `sentry-sdk[fastapi]` is installed — it hooks into ASGI middleware, captures unhandled exceptions, and adds request context.

#### D. Frontend — `frontend/package.json`
Add:
```
"@sentry/react": "^9.0"
```

#### E. Frontend — `frontend/src/main.tsx` (or wherever the React app initializes)
```typescript
import * as Sentry from "@sentry/react";

if (import.meta.env.VITE_SENTRY_DSN) {
  Sentry.init({
    dsn: import.meta.env.VITE_SENTRY_DSN,
    integrations: [Sentry.browserTracingIntegration()],
    tracesSampleRate: 0.1,
  });
}
```

#### F. Environment variables
Add to `docker-compose.yml` backend service:
```yaml
SENTRY_DSN: ${SENTRY_DSN:-}
```

Add to `frontend/.env.production` (or build-time arg):
```
VITE_SENTRY_DSN=<frontend-dsn>
```

**Note:** Frontend and backend should use separate Sentry projects (different DSNs) since they're different platforms.

#### G. Frontend Dockerfile
Pass the Sentry DSN as a build arg so Vite can inline it:
```dockerfile
ARG VITE_SENTRY_DSN=""
ENV VITE_SENTRY_DSN=$VITE_SENTRY_DSN
```

### Setup steps (manual, one-time)
1. Create free Sentry account at sentry.io
2. Create two projects: one Python (FastAPI), one JavaScript (React)
3. Copy DSNs into `.env` file on server
4. DSNs are safe to embed in frontend code (they're write-only ingest URLs)

### What this gives you
- Automatic capture of all unhandled exceptions (backend + frontend)
- Stack traces with local variables
- Request context (URL, headers, user info)
- Browser errors (JS exceptions, network failures)
- Performance monitoring (slow endpoints, slow page loads)
- Alerts via email/Slack when new errors appear

---

## 3. Uptime Monitoring

### Current state
- Backend has a `/api/health` endpoint (returns 200 OK)
- No external monitoring exists

### Approach — External service (no code changes needed)

This requires **zero code changes**. Use an external service to ping your health endpoint.

#### Option A: UptimeRobot (Recommended — free tier)
1. Sign up at uptimerobot.com (free: 50 monitors, 5-min intervals)
2. Add HTTP(S) monitor: `https://${DOMAIN}/api/health`
3. Set check interval: 5 minutes
4. Configure alerts: email + optional Slack/Telegram webhook
5. Add a second monitor for the frontend: `https://${DOMAIN}` (checks HTTP 200)

#### Option B: Better Stack (alternative)
1. Sign up at betterstack.com (free tier available)
2. Same setup as above

#### Optional enhancement — Enrich the health endpoint
Currently the health endpoint likely just returns `{"status": "ok"}`. Consider adding:
```python
@app.get("/api/health")
async def health(db: AsyncSession = Depends(get_db)):
    # Check DB connectivity
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "unhealthy", "db": "down"}, status_code=503)
    return {"status": "ok", "db": "up"}
```

This way, the uptime monitor will catch database failures too, not just "is the Python process alive."

### What this gives you
- Instant notification when your site goes down
- Uptime percentage tracking (useful for SLAs with customers)
- Response time monitoring (catch slow performance before customers complain)
- Public status page (optional, good for customer trust)

---

## 4. Restrict SSH in Terraform

### Current state
```hcl
# SSH — currently open to the entire internet
ingress_security_rules {
    protocol = "6"
    source   = "0.0.0.0/0"
    tcp_options {
        min = 22
        max = 22
    }
}
```

### Changes

#### A. `deploy/main.tf` — Add SSH CIDR variable
```hcl
variable "ssh_allowed_cidr" {
  description = "CIDR block allowed to SSH (e.g., your home IP: 1.2.3.4/32)"
  default     = "0.0.0.0/0"  # Default still open — user MUST override
}
```

#### B. `deploy/main.tf` — Update security rule
Change the SSH ingress rule:
```hcl
ingress_security_rules {
    protocol = "6"
    source   = var.ssh_allowed_cidr
    tcp_options {
        min = 22
        max = 22
    }
}
```

#### C. `deploy/terraform.tfvars.example` (create if not exists)
```hcl
ssh_allowed_cidr = "YOUR.PUBLIC.IP/32"  # Find your IP: curl ifconfig.me
```

### Application
- Run `terraform plan` to preview the change
- Run `terraform apply` to apply
- Test SSH still works from your IP
- Verify SSH is blocked from other IPs

### Fallback access
If you lock yourself out (IP changes), you can:
1. Update `ssh_allowed_cidr` and re-apply Terraform
2. Use OCI Console → Cloud Shell → Instance Console Connection (always available)
3. Use OCI Bastion service (free, on-demand SSH tunneling)

---

## Implementation Order

```
Step 1: SSH restriction (#4)          — 5 min, zero risk, immediate security win
Step 2: Uptime monitoring (#3)        — 15 min, zero code changes, external service
Step 3: Sentry integration (#2)       — 30–45 min, small code changes, big observability win
Step 4: HTTPS with Let's Encrypt (#1) — 2–3 hours, largest change, most moving parts
```

Rationale: Start with the smallest/safest changes first. HTTPS is last because it touches the most files and has the cert bootstrapping complexity — better to have error tracking and monitoring in place before making infrastructure changes.
