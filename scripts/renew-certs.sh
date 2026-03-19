#!/bin/bash
# Renew Let's Encrypt certificates and reload Nginx.
# Intended to run via cron (e.g. daily at 3 AM).
set -euo pipefail

cd /opt/wijnpick

docker compose run --rm certbot renew --quiet
docker compose exec nginx nginx -s reload
