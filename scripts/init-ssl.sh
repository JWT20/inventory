#!/bin/bash
# Obtain Let's Encrypt certificate and switch Nginx to HTTPS.
# Usage: ./scripts/init-ssl.sh
# Requires: DOMAIN and CERT_EMAIL set in .env (or exported)
set -euo pipefail

DOMAIN="${DOMAIN:?Set DOMAIN in .env}"
CERT_EMAIL="${CERT_EMAIL:-admin@$DOMAIN}"

echo "==> Obtaining SSL certificate for $DOMAIN ..."

# 1. Make sure containers are running with HTTP-only config
docker compose up -d nginx

# 2. Request certificate via webroot challenge
docker compose run --rm certbot certonly \
  --webroot -w /var/www/certbot \
  -d "$DOMAIN" \
  --email "$CERT_EMAIL" \
  --agree-tos \
  --non-interactive

# 3. Generate the TLS nginx config from template
export DOMAIN
envsubst '${DOMAIN}' < nginx/nginx.conf.template > nginx/nginx-ssl.conf

# 4. Swap in the TLS config and reload
cp nginx/nginx-ssl.conf nginx/nginx.conf
docker compose exec nginx nginx -s reload

echo "==> HTTPS is live at https://$DOMAIN"
