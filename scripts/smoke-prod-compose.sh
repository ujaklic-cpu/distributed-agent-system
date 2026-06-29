#!/usr/bin/env bash
set -euo pipefail

COMPOSE=${COMPOSE:-docker compose}
COMPOSE_FILE=${COMPOSE_FILE:-deploy/docker-compose.prod.yml}
LOG_DIR=${LOG_DIR:-logs/smoke-prod-compose}
ENV_FILE=$(mktemp .env.step7.1.prod.XXXXXX)
mkdir -p "$LOG_DIR"
cleanup() {
  $COMPOSE --env-file "$ENV_FILE" -f "$COMPOSE_FILE" down -v >>"$LOG_DIR/compose-down.log" 2>&1 || true
  rm -f "$ENV_FILE"
}
trap cleanup EXIT

rand() { python - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
}

pg_password=$(rand)
redis_password=$(rand)
cluster_token=$(rand)
node_token=$(rand)
operator_token=$(rand)
admin_token=$(rand)
readonly_token=$(rand)
minio_user="minioadmin$(rand | tr -dc 'A-Za-z0-9' | head -c 8)"
minio_password=$(rand)

cp .env.production.example "$ENV_FILE"
cat >>"$ENV_FILE" <<EOF
ENVIRONMENT=production
POSTGRES_DB=agents
POSTGRES_USER=agents
POSTGRES_PASSWORD=$pg_password
REDIS_PASSWORD=$redis_password
MINIO_ROOT_USER=$minio_user
MINIO_ROOT_PASSWORD=$minio_password
CLUSTER_TOKEN=$cluster_token
NODE_TOKENS=$node_token
OPERATOR_TOKENS=$operator_token
ADMIN_TOKENS=$admin_token
READONLY_TOKENS=$readonly_token
DEPRECATED_TOKENS=
CORS_ALLOW_ORIGINS=https://ops.example.invalid
DATABASE_URL=postgresql+psycopg://agents:$pg_password@postgres:5432/agents
QUEUE_URL=redis://:$redis_password@redis:6379/0
ORCHESTRATOR_IMAGE=local-agent/orchestrator-api:7.1
NODE_AGENT_IMAGE=local-agent/node-agent:7.1
NODE_AGENT_VERSION=0.7.0
EOF

python - <<'PY'
from pathlib import Path
text = Path('deploy/docker-compose.prod.yml').read_text()
node = text.split('  node-agent:', 1)[1].split('\n  ', 1)[0]
if '/var/run/docker.sock' in node:
    raise SystemExit('node-agent directly mounts docker.sock')
if 'docker-socket-proxy:' not in text:
    raise SystemExit('docker-socket-proxy service is missing')
PY

$COMPOSE --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d >>"$LOG_DIR/compose-up.log" 2>&1
$COMPOSE --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps >>"$LOG_DIR/compose-ps.log" 2>&1

base="http://127.1.0.1:8080"
for _ in $(seq 1 60); do
  if curl -fsS "$base/health" >>"$LOG_DIR/http.log" 2>&1; then
    break
  fi
  sleep 2
done
curl -fsS "$base/health" >>"$LOG_DIR/http.log"
curl -i "$base/v1/tasks" >>"$LOG_DIR/http.log" 2>&1 || true
curl -fsS -H "Authorization: Bearer $readonly_token" "$base/metrics" >>"$LOG_DIR/http.log"
curl -fsS -H "Authorization: Bearer $operator_token" "$base/v1/tasks" >>"$LOG_DIR/http.log"
curl -i -H "Authorization: Bearer $operator_token" "$base/v1/memory" >>"$LOG_DIR/http.log" 2>&1 || true
