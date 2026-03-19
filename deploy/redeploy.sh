#!/bin/bash
# Redeploy WijnPick app (for updates, skips Caddy/infra setup)
# Usage: ./redeploy.sh <vm-ip-adres>
set -euo pipefail

if [ $# -eq 0 ]; then
    echo "Usage: ./redeploy.sh <vm-ip-adres>"
    exit 1
fi

VM_IP="$1"
SSH_USER="opc"
APP_DIR="/opt/wijnpick"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== WijnPick Redeploy naar ${VM_IP} ==="
echo ""

echo "1/4 App-bestanden kopieren..."
rsync -avz --delete \
    --exclude '.git' --exclude 'deploy' --exclude '.env' --exclude '__pycache__' \
    "${PROJECT_ROOT}/" "${SSH_USER}@${VM_IP}:${APP_DIR}/"
# Sync Caddyfile separately (deploy/ is excluded from main rsync)
rsync -avz "${PROJECT_ROOT}/deploy/Caddyfile" "${SSH_USER}@${VM_IP}:${APP_DIR}/deploy/"

echo "2/4 Caddy config updaten..."
ssh "${SSH_USER}@${VM_IP}" "set -a; source ${APP_DIR}/.env; set +a; envsubst '\${DOMAIN}' < ${APP_DIR}/deploy/Caddyfile | sudo tee /etc/caddy/Caddyfile > /dev/null && sudo systemctl reload caddy"

echo "3/4 Containers rebuilden en herstarten..."
ssh "${SSH_USER}@${VM_IP}" "cd ${APP_DIR} && sudo docker compose build && sudo docker compose up -d"

echo "4/4 Status controleren..."
ssh "${SSH_USER}@${VM_IP}" "cd ${APP_DIR} && sudo docker compose ps && echo '' && sudo systemctl status caddy --no-pager -l"

echo ""
echo "=== Redeploy compleet! ==="
