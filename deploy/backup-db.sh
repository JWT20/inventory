#!/bin/bash
# Nightly pg_dump to OCI Object Storage (Always Free 20 GB bucket).
# Keeps 3 rolling daily backups: backup-0.sql.gz (today), backup-1 (yesterday), backup-2 (day before).
# Runs inside the host, exec-ing into the db container.

set -euo pipefail

BUCKET="${OCI_BACKUP_BUCKET:-wijnpick-backups}"
NAMESPACE=$(oci os ns get --query 'data' --raw-output)
COMPOSE_DIR="/opt/wijnpick"
DUMP_DIR="/tmp/wijnpick-backup"

mkdir -p "$DUMP_DIR"

echo "[backup] $(date -Iseconds) Starting pg_dump..."

# Dump from the running db container
docker compose -f "$COMPOSE_DIR/docker-compose.yml" exec -T db \
  pg_dump -U "${POSTGRES_USER:-wijnpick}" -Fc "${POSTGRES_DB:-wijnpick}" \
  | gzip > "$DUMP_DIR/backup-new.sql.gz"

FILESIZE=$(stat -c%s "$DUMP_DIR/backup-new.sql.gz")
echo "[backup] Dump size: $((FILESIZE / 1024)) KB"

# Rotate: delete oldest (backup-2), rename 1->2, 0->1, upload new as 0
echo "[backup] Rotating backups in bucket '$BUCKET'..."

oci os object delete -bn "$BUCKET" --name "backup-2.sql.gz" --force --namespace "$NAMESPACE" 2>/dev/null || true
oci os object rename -bn "$BUCKET" --source-name "backup-1.sql.gz" --new-name "backup-2.sql.gz" --namespace "$NAMESPACE" 2>/dev/null || true
oci os object rename -bn "$BUCKET" --source-name "backup-0.sql.gz" --new-name "backup-1.sql.gz" --namespace "$NAMESPACE" 2>/dev/null || true

echo "[backup] Uploading new backup as backup-0.sql.gz..."
oci os object put -bn "$BUCKET" --name "backup-0.sql.gz" --file "$DUMP_DIR/backup-new.sql.gz" --force --namespace "$NAMESPACE"

# Cleanup local temp
rm -f "$DUMP_DIR/backup-new.sql.gz"

echo "[backup] $(date -Iseconds) Done."
