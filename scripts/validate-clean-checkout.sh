#!/usr/bin/env bash
set -euo pipefail

python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
make lint
python -m ruff format --check .
make typecheck
make test-unit

if [ "${RUN_DOCKER_INTEGRATION_TESTS:-0}" = "1" ]; then
  make test-integration
fi
