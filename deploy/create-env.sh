#!/usr/bin/env bash
# Create /opt/wijnpick/.env for a fresh server without committing secrets.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/wijnpick}"
ENV_FILE="${ENV_FILE:-${APP_DIR}/.env}"

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "Set ${name} before running this script." >&2
    exit 1
  fi
}

require_url_safe() {
  local name="$1"
  local value="$2"
  if [[ ! "$value" =~ ^[A-Za-z0-9._~-]+$ ]]; then
    echo "${name} must contain only URL-safe characters: A-Z a-z 0-9 . _ ~ -" >&2
    exit 1
  fi
}

# Looser check for values that go straight into the .env file as-is.
# Rejects characters that would break Docker Compose env_file parsing or
# trigger ${...} interpolation: newlines, '$', and non-printable bytes.
require_envfile_safe() {
  local name="$1"
  local value="$2"
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    echo "${name} must not contain newlines." >&2
    exit 1
  fi
  if [[ "$value" == *'$'* ]]; then
    echo "${name} must not contain '\$' (Docker Compose would interpolate it)." >&2
    exit 1
  fi
  if [[ "$value" =~ [^[:print:]] ]]; then
    echo "${name} must contain only printable ASCII characters." >&2
    exit 1
  fi
}

require_channel_credential_key() {
  local value="$1"
  # A 32-byte value encodes to 43 base64url characters, optionally followed
  # by one padding character. This is the format accepted by the backend.
  if [[ ! "$value" =~ ^[A-Za-z0-9_-]{43}=?$ ]]; then
    echo \
      "CHANNEL_CREDENTIAL_ENCRYPTION_KEY must be a base64url-encoded 32-byte key." \
      >&2
    exit 1
  fi
}

generate_channel_credential_key() {
  openssl rand -base64 32 | tr '+/' '-_' | tr -d '=\r\n'
}

if [[ -f "$ENV_FILE" && "${FORCE:-0}" != "1" ]]; then
  echo "${ENV_FILE} already exists. Set FORCE=1 to replace it." >&2
  exit 1
fi

require_env GEMINI_API_KEY
require_env ADMIN_PASSWORD

POSTGRES_USER="${POSTGRES_USER:-wijnpick}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$(openssl rand -hex 32)}"
POSTGRES_DB="${POSTGRES_DB:-wijnpick}"
SECRET_KEY="${SECRET_KEY:-$(openssl rand -hex 32)}"
CHANNEL_CREDENTIAL_ENCRYPTION_KEY="${CHANNEL_CREDENTIAL_ENCRYPTION_KEY:-$(generate_channel_credential_key)}"
DOMAIN="${DOMAIN:-dockscan.nl}"

require_url_safe POSTGRES_PASSWORD "$POSTGRES_PASSWORD"
require_envfile_safe SECRET_KEY     "$SECRET_KEY"
require_envfile_safe CHANNEL_CREDENTIAL_ENCRYPTION_KEY "$CHANNEL_CREDENTIAL_ENCRYPTION_KEY"
require_channel_credential_key "$CHANNEL_CREDENTIAL_ENCRYPTION_KEY"
require_envfile_safe ADMIN_PASSWORD "$ADMIN_PASSWORD"
require_envfile_safe GEMINI_API_KEY "$GEMINI_API_KEY"
require_envfile_safe DOMAIN         "$DOMAIN"
require_envfile_safe VAPID_PUBLIC_KEY "${VAPID_PUBLIC_KEY:-}"
require_envfile_safe VAPID_PRIVATE_KEY "${VAPID_PRIVATE_KEY:-}"
require_envfile_safe VAPID_SUBJECT "${VAPID_SUBJECT:-https://${DOMAIN}}"

tmp_file="$(mktemp)"
trap 'rm -f "$tmp_file"' EXIT

cat > "$tmp_file" <<ENVEOF
# Database
POSTGRES_USER=${POSTGRES_USER}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
POSTGRES_DB=${POSTGRES_DB}
DATABASE_URL=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB}

# Google Gemini
GEMINI_API_KEY=${GEMINI_API_KEY}
GEMINI_VISION_MODEL=${GEMINI_VISION_MODEL:-gemini-2.5-flash}
GEMINI_EXTRACTION_MODEL=${GEMINI_EXTRACTION_MODEL:-gemini-2.5-pro}
GEMINI_EMBEDDING_MODEL=${GEMINI_EMBEDDING_MODEL:-gemini-embedding-001}

# Backend
SECRET_KEY=${SECRET_KEY}
MATCH_THRESHOLD=${MATCH_THRESHOLD:-0.80}
UPLOAD_DIR=${UPLOAD_DIR:-/app/uploads}

# Auth
ADMIN_PASSWORD=${ADMIN_PASSWORD}
ACCESS_TOKEN_EXPIRE_MINUTES=${ACCESS_TOKEN_EXPIRE_MINUTES:-30}
REFRESH_TOKEN_EXPIRE_DAYS=${REFRESH_TOKEN_EXPIRE_DAYS:-7}

# Web Push (empty key pair disables the bell and dispatcher)
VAPID_PUBLIC_KEY=${VAPID_PUBLIC_KEY:-}
VAPID_PRIVATE_KEY=${VAPID_PRIVATE_KEY:-}
VAPID_SUBJECT=${VAPID_SUBJECT:-https://${DOMAIN}}
PUSH_DISPATCH_INTERVAL_SECONDS=${PUSH_DISPATCH_INTERVAL_SECONDS:-5}

# Storage backend ("local" or "s3")
STORAGE_BACKEND=${STORAGE_BACKEND:-local}
S3_ENDPOINT_URL=${S3_ENDPOINT_URL:-}
S3_BUCKET=${S3_BUCKET:-}
S3_ACCESS_KEY_ID=${S3_ACCESS_KEY_ID:-}
S3_SECRET_ACCESS_KEY=${S3_SECRET_ACCESS_KEY:-}
S3_REGION=${S3_REGION:-}
S3_PRESIGNED_URL_EXPIRY=${S3_PRESIGNED_URL_EXPIRY:-3600}

# Kafka
KAFKA_BOOTSTRAP_SERVERS=${KAFKA_BOOTSTRAP_SERVERS:-kafka:9092}

# Langfuse
LANGFUSE_PUBLIC_KEY=${LANGFUSE_PUBLIC_KEY:-}
LANGFUSE_SECRET_KEY=${LANGFUSE_SECRET_KEY:-}
LANGFUSE_HOST=${LANGFUSE_HOST:-https://cloud.langfuse.com}

# Sentry
SENTRY_DSN=${SENTRY_DSN:-}
VITE_SENTRY_DSN=${VITE_SENTRY_DSN:-}

# Domain
DOMAIN=${DOMAIN}

# Shopify credential encryption
CHANNEL_CREDENTIAL_ENCRYPTION_KEY=${CHANNEL_CREDENTIAL_ENCRYPTION_KEY}

# Veloyd label verification
VELOYD_API_KEY=${VELOYD_API_KEY:-}
VELOYD_API_BASE_URL=${VELOYD_API_BASE_URL:-https://app.veloyd.nl/api}
ENVEOF

install -d -m 0755 "$APP_DIR"
install -m 0600 "$tmp_file" "$ENV_FILE"

if [[ $EUID -eq 0 ]] && id deploy >/dev/null 2>&1; then
  chown deploy:deploy "$ENV_FILE"
fi

echo "Created ${ENV_FILE}"
