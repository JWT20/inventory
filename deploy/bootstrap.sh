#!/usr/bin/env bash
# One-shot bootstrap for a fresh Contabo VPS (Ubuntu 24.04) running wijnpick.
#
# Run as root on the new server, e.g.:
#   scp deploy/bootstrap.sh root@<new-vps-ip>:/root/
#   ssh root@<new-vps-ip> "DEPLOY_PUBKEY='ssh-ed25519 AAAA... user' bash /root/bootstrap.sh"
#
# Idempotent: safe to re-run. After this completes, set the GitHub Actions
# secrets SERVER_IP and SSH_PRIVATE_KEY, then push to main to deploy the app.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

if [[ -z "${DEPLOY_PUBKEY:-}" ]]; then
  echo "Set DEPLOY_PUBKEY env var to the public SSH key for the deploy user." >&2
  echo "Example: DEPLOY_PUBKEY='ssh-ed25519 AAAA... me@laptop' bash bootstrap.sh" >&2
  exit 1
fi

echo "==> apt update + base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y \
  ca-certificates curl gnupg git rsync ufw gettext-base debian-keyring debian-archive-keyring apt-transport-https

echo "==> Docker (official repo)"
install -m 0755 -d /etc/apt/keyrings
if [[ ! -f /etc/apt/keyrings/docker.asc ]]; then
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
fi
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker

echo "==> Caddy (official repo)"
if [[ ! -f /etc/apt/sources.list.d/caddy-stable.list ]]; then
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y
fi
apt-get install -y caddy
systemctl enable caddy

# Caddy access log directory (referenced by Caddyfile)
install -d -o caddy -g caddy -m 0755 /var/log/caddy

echo "==> deploy user"
if ! id deploy >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" deploy
fi
usermod -aG sudo,docker deploy
echo "deploy ALL=(ALL) NOPASSWD: /bin/systemctl reload caddy, /usr/bin/tee /etc/caddy/Caddyfile, /bin/systemctl status caddy, /usr/bin/docker, /usr/bin/docker compose" \
  > /etc/sudoers.d/deploy
chmod 440 /etc/sudoers.d/deploy
install -d -o deploy -g deploy -m 0700 /home/deploy/.ssh
echo "${DEPLOY_PUBKEY}" > /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys

echo "==> /opt/wijnpick"
install -d -o deploy -g deploy -m 0755 /opt/wijnpick
install -d -o deploy -g deploy -m 0755 /opt/wijnpick/deploy

echo "==> UFW (SSH + HTTP + HTTPS)"
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "==> Unattended-upgrades: night-only window + auto-reboot + netdata refresh"
apt-get install -y unattended-upgrades

# 1. Move the auto-upgrade off business hours. The stock apt-daily(-upgrade) timers
#    fire mid-morning (~06:46 here), and a systemd/udev upgrade at that time re-execs
#    systemd and reshuffles cgroups right as people start working. Run download at
#    03:00 and install at 03:30 instead, when nobody is using the app.
for timer in apt-daily apt-daily-upgrade; do
  install -d "/etc/systemd/system/${timer}.timer.d"
done
cat > /etc/systemd/system/apt-daily.timer.d/override.conf <<'EOF'
[Timer]
# Clear the stock twice-daily schedule, then download updates at 03:00.
OnCalendar=
OnCalendar=*-*-* 03:00
RandomizedDelaySec=30m
Persistent=true
EOF
cat > /etc/systemd/system/apt-daily-upgrade.timer.d/override.conf <<'EOF'
[Timer]
# Install the downloaded updates at 03:30, after the download window.
OnCalendar=
OnCalendar=*-*-* 03:30
RandomizedDelaySec=15m
Persistent=true
EOF

# 2. Let unattended-upgrades reboot when a kernel/systemd upgrade requires it, so
#    services never keep running against half-replaced libsystemd/cgroup state.
#    Reboot at 05:00, even if a user is logged in. Automatic-Reboot-Time is an
#    absolute wall-clock time handed to `shutdown -r`: the install run can start
#    as late as 03:45 (03:30 + RandomizedDelaySec) and then take a few minutes,
#    so a tighter time risks finishing *after* it -- which puts the target in the
#    past and lets the reboot slip to the next day. Keep generous headroom; the
#    app is idle at night, so a later reboot costs nothing.
cat > /etc/apt/apt.conf.d/52unattended-upgrades-local <<'EOF'
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-WithUsers "true";
Unattended-Upgrade::Automatic-Reboot-Time "05:00";
EOF

# 3. Belt-and-braces for upgrades that do NOT trigger a reboot: hand netdata a fresh
#    start after every package transaction. After a systemd/udev upgrade netdata's
#    cgroup collector can hold stale handles and hammer the Docker API, so dockerd +
#    containerd burn ~140% CPU until netdata is restarted. try-restart is a no-op
#    when netdata is not running, so this stays safe on hosts without it.
cat > /etc/apt/apt.conf.d/99restart-netdata <<'EOF'
DPkg::Post-Invoke-Success { "if systemctl is-active --quiet netdata; then systemctl try-restart netdata >/dev/null 2>&1 || true; fi"; };
EOF

systemctl daemon-reload
systemctl restart apt-daily.timer apt-daily-upgrade.timer

cat <<'NEXT'

==> bootstrap complete

Next steps:
  1. Set DNS in Cloudflare: A record for your domain -> this server IP
  2. Copy deploy/create-env.sh to the server and run it as deploy:
       scp deploy/create-env.sh deploy@<new-vps-ip>:/opt/wijnpick/deploy/
       ssh deploy@<new-vps-ip> "GEMINI_API_KEY='...' ADMIN_PASSWORD='...' DOMAIN='dockscan.nl' bash /opt/wijnpick/deploy/create-env.sh"
  3. In GitHub repo, set Actions secrets:
       SERVER_IP        = this server's public IP
       SSH_PRIVATE_KEY  = private key matching DEPLOY_PUBKEY
  4. Push to main -> .github/workflows/deploy.yml will rsync code and run
     docker compose up. Caddy will obtain a TLS cert on first request.
NEXT
