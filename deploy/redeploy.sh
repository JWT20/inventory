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

echo "1/3 App-bestanden kopieren..."
rsync -avz --delete \
    --exclude '.git' --exclude 'deploy' --exclude '.env' --exclude '__pycache__' \
    "${PROJECT_ROOT}/" "${SSH_USER}@${VM_IP}:${APP_DIR}/"

echo "2/3 Containers rebuilden en herstarten..."
ssh "${SSH_USER}@${VM_IP}" "cd ${APP_DIR} && sudo docker compose build && sudo docker compose up -d"

echo "3/3 Status controleren..."
ssh "${SSH_USER}@${VM_IP}" "cd ${APP_DIR} && sudo docker compose ps"

echo ""
echo "=== Redeploy compleet! ==="
