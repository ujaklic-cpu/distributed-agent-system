#!/usr/bin/env sh
set -eu
BACKUP_DIR=${BACKUP_DIR:-./backups/minio}
TS=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$BACKUP_DIR/minio-$TS"
docker run --rm --network orchestrator-prod_backend \
  -v "$(pwd)/$BACKUP_DIR/minio-$TS:/backup" \
  -v "$(pwd)/secrets:/secrets:ro" \
  minio/mc:latest sh -c '
    mc alias set prod http://minio:9000 "$(cat /secrets/minio_access_key)" "$(cat /secrets/minio_secret_key)" >/dev/null
    mc mirror --preserve --overwrite prod /backup
  '
tar -C "$BACKUP_DIR" -czf "$BACKUP_DIR/minio-$TS.tar.gz" "minio-$TS"
rm -rf "$BACKUP_DIR/minio-$TS"
sha256sum "$BACKUP_DIR/minio-$TS.tar.gz" > "$BACKUP_DIR/minio-$TS.tar.gz.sha256"
echo "$BACKUP_DIR/minio-$TS.tar.gz"
