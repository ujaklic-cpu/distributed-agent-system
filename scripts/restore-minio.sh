#!/usr/bin/env sh
set -eu
if [ $# -ne 1 ]; then
  echo "usage: $0 backups/minio/minio-YYYYMMDDTHHMMSSZ.tar.gz" >&2
  exit 2
fi
ARCHIVE=$1
test -f "$ARCHIVE"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
tar -C "$TMP" -xzf "$ARCHIVE"
SRC=$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -n 1)
docker run --rm --network orchestrator-prod_backend \
  -v "$SRC:/backup:ro" \
  -v "$(pwd)/secrets:/secrets:ro" \
  minio/mc:latest sh -c '
    mc alias set prod http://minio:9000 "$(cat /secrets/minio_access_key)" "$(cat /secrets/minio_secret_key)" >/dev/null
    mc mirror --overwrite /backup prod
  '
