#!/bin/bash
# Deploy WijnPick app to the provisioned OCI VM
# Gebruik: ./setup.sh <vm-ip-adres>
set -euo pipefail

if [ $# -eq 0 ]; then
    echo "Gebruik: ./setup.sh <vm-ip-adres>"
    echo "  Het IP-adres vind je via: cd deploy && terraform output vm_public_ip"
    exit 1
fi

VM_IP="$1"
SSH_USER="opc"
APP_DIR="/opt/wijnpick"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== WijnPick Deployment naar ${VM_IP} ==="
echo ""

# Copy project files to VM
echo "1/4 App-bestanden kopieren..."
rsync -avz --exclude '.git' --exclude 'deploy' --exclude '.env' --exclude '__pycache__' \
    "${PROJECT_ROOT}/" "${SSH_USER}@${VM_IP}:${APP_DIR}/"

# Setup HTTPS with Caddy
echo "2/4 Caddy reverse proxy installeren voor HTTPS..."
ssh "${SSH_USER}@${VM_IP}" << 'REMOTE_SCRIPT'
set -euo pipefail

# Read domain from .env
source /opt/wijnpick/.env

# Install Caddy
sudo dnf install -y 'dnf-command(copr)' || true
sudo dnf copr enable -y @caddy/caddy || true
sudo dnf install -y caddy || {
    # Fallback: direct binary install for aarch64
    curl -sL "https://caddyserver.com/api/download?os=linux&arch=arm64" -o /tmp/caddy
    sudo mv /tmp/caddy /usr/bin/caddy
    sudo chmod +x /usr/bin/caddy
    sudo groupadd --system caddy 2>/dev/null || true
    sudo useradd --system --gid caddy --create-home --home-dir /var/lib/caddy --shell /usr/sbin/nologin caddy 2>/dev/null || true
}

# Caddy config
sudo mkdir -p /etc/caddy
sudo tee /etc/caddy/Caddyfile > /dev/null << CADDYEOF
${DOMAIN} {
    reverse_proxy localhost:8080
}
CADDYEOF

# Caddy systemd service
sudo tee /etc/systemd/system/caddy.service > /dev/null << 'SERVICEEOF'
[Unit]
Description=Caddy
After=network.target

[Service]
User=caddy
Group=caddy
ExecStart=/usr/bin/caddy run --environ --config /etc/caddy/Caddyfile
ExecReload=/usr/bin/caddy reload --config /etc/caddy/Caddyfile
TimeoutStopSec=5s
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
SERVICEEOF

sudo systemctl daemon-reload
sudo systemctl enable caddy
REMOTE_SCRIPT

# Start the application
echo "3/4 Docker Compose starten..."
ssh "${SSH_USER}@${VM_IP}" << 'REMOTE_SCRIPT'
set -euo pipefail
cd /opt/wijnpick

# Build and start
sudo docker compose build
sudo docker compose up -d

# Start Caddy for HTTPS
sudo systemctl start caddy
REMOTE_SCRIPT

echo "4/4 Verificatie..."
ssh "${SSH_USER}@${VM_IP}" << 'REMOTE_SCRIPT'
echo "Docker containers:"
sudo docker compose -f /opt/wijnpick/docker-compose.yml ps
echo ""
echo "Caddy status:"
sudo systemctl status caddy --no-pager -l || true
REMOTE_SCRIPT

echo ""
echo "=== Deployment compleet! ==="
# Read domain from terraform
DOMAIN=$(cd "$(dirname "$0")" && terraform output -raw app_url 2>/dev/null || echo "https://dockscan.nl")
echo "App beschikbaar op: ${DOMAIN}"
echo ""
echo "SSH toegang: ssh ${SSH_USER}@${VM_IP}"
echo "Logs bekijken: ssh ${SSH_USER}@${VM_IP} 'cd /opt/wijnpick && sudo docker compose logs -f'"
