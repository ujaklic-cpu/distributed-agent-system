#!/bin/sh
set -e

python -m orchestrator.worker &

exec python -m orchestrator.api
