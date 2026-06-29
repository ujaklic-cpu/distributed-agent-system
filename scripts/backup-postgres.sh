#!/usr/bin/env sh
set -eu
BACKUP_DIR=${BACKUP_DIR:-./backups/postgres}
TS=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$BACKUP_DIR"
docker compose -f docker-compose.prod.yml exec -T postgres \
  pg_dump -U "${POSTGRES_USER:-orchestrator}" -d "${POSTGRES_DB:-orchestrator}" --format=custom --no-owner --no-acl \
  > "$BACKUP_DIR/postgres-$TS.dump"
sha256sum "$BACKUP_DIR/postgres-$TS.dump" > "$BACKUP_DIR/postgres-$TS.dump.sha256"
echo "$BACKUP_DIR/postgres-$TS.dump"
