#!/usr/bin/env sh
set -eu
if [ $# -ne 1 ]; then
  echo "usage: $0 backups/postgres/postgres-YYYYMMDDTHHMMSSZ.dump" >&2
  exit 2
fi
BACKUP_FILE=$1
test -f "$BACKUP_FILE"
docker compose -f docker-compose.prod.yml exec -T postgres \
  psql -U "${POSTGRES_USER:-orchestrator}" -d "${POSTGRES_DB:-orchestrator}" -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${POSTGRES_DB:-orchestrator}' AND pid <> pg_backend_pid();"
docker compose -f docker-compose.prod.yml exec -T postgres \
  dropdb -U "${POSTGRES_USER:-orchestrator}" --if-exists "${POSTGRES_DB:-orchestrator}"
docker compose -f docker-compose.prod.yml exec -T postgres \
  createdb -U "${POSTGRES_USER:-orchestrator}" "${POSTGRES_DB:-orchestrator}"
docker compose -f docker-compose.prod.yml exec -T postgres \
  pg_restore -U "${POSTGRES_USER:-orchestrator}" -d "${POSTGRES_DB:-orchestrator}" --clean --if-exists --no-owner --no-acl < "$BACKUP_FILE"
