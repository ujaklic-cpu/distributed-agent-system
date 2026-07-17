# Distributed agent system

This project runs delegated model tasks across one or more worker nodes. A FastAPI orchestrator stores task state in Postgres, queues work in Redis, assigns jobs to node agents, and indexes output in a local artifact store. Node agents run model requests and disposable tool containers. Tool containers receive one workspace mount and never receive Docker socket access.

## Components

- `orchestrator/`: task API, scheduler, state machine, persistence, verification, memory, and artifact indexing
- `node_agent/`: node registration, capacity reporting, job execution, draining, and cancellation
- `agent_server/`: mock and Ollama-backed model API
- `tool-images/`: shared runner and per-tool container images
- `shared/`: request models, authentication, policy, logging, and redaction
- `deploy/`: local, production, and additional-node Compose files
- `tests/`: Docker-free unit tests and opt-in Compose integration tests

See [architecture](docs/architecture.md), [deployment](docs/deployment.md), and [operations](docs/operations.md) for service boundaries and operating procedures.

## Local checks

Python 3.11 is the supported CI version.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy shared orchestrator node_agent agent_server
.venv/bin/python -m pytest -q -m "not integration"
```

The equivalent Make targets are `make install`, `make lint`, `make typecheck`, and `make test-unit`.

## Local Compose stack

Create `.env.test` with local-only values, then start the control stack:

```bash
docker compose -f deploy/docker-compose.control.yml --env-file .env.test up --build -d
```

Submit and inspect a task through the orchestrator on port 8080:

```bash
curl -sS -X POST http://localhost:8080/tasks \
  -H 'content-type: application/json' \
  -d '{"goal":"Create output/result.md.","priority":5,"workspace_id":"demo","mode":"normal"}'
curl -sS http://localhost:8080/tasks/<task_id>
curl -sS http://localhost:8080/tasks/<task_id>/result
```

Run the Compose integration suite only when Docker is available:

```bash
RUN_DOCKER_INTEGRATION_TESTS=1 .venv/bin/python -m pytest -q -m integration tests/integration
```

## Security boundary

The node agent creates tool containers through the Compose-managed Docker socket proxy. Each tool has explicit CPU, memory, timeout, network, and workspace-access policy. Production configuration rejects shared default credentials, missing Redis authentication, SQLite, wildcard CORS, and missing role-specific tokens.

Host-process tool fallback is intended only for local development. Production deployments must leave `NODE_AGENT_ALLOW_HOST_TOOL_FALLBACK` disabled and must not mount `/var/run/docker.sock` into node-agent or tool containers.

Apply database migrations before starting services outside Compose:

```bash
alembic upgrade head
```

The production validation entry points are `scripts/validate-clean-checkout.sh` and `scripts/smoke-prod-compose.sh`.
