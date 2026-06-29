# Distributed Local Multi-Agent System - Phase 5 MVP

This repository implements Phase 5 of the technical specification: the multi-machine node layer, model classes and delegation, the full deterministic Docker-based tool plane, and the long-running operation layer. Core services include:

- FastAPI orchestrator
- Redis-backed task queue
- Postgres task state
- node-agent service
- smart/worker agent-server containers
- disposable Docker-based shell/test/internet tool containers
- deterministic tool policy
- bounded retries, tool calls, and agent steps
- local workspace layout
- persistent node registry and job records
- node-agent self-registration and heartbeat loop
- resource-aware scheduler for agent and tool jobs
- stale-node detection and task requeueing
- difficulty/risk-based routing to smart, code, and fast workers
- mandatory smart review for code-worker output
- task-mode-aware model downgrade rules
- full tool plane with shell, internet, test, filesystem, build, and analysis tools
- per-tool CPU, memory, timeout, and output limits
- persisted tool-run records and structured tool audit logs
- separate local Docker images for each tool class
- bounded post-success optimization rounds
- mandatory final verification after optimization
- local artifact indexing and artifact-store metadata
- persistent memory records
- protected machine metrics, human dashboard, structured audit logs, and correlation IDs

The MVP is intentionally conservative: AI containers can ask for tool execution, but only the orchestrator validates and schedules tool jobs. The node agent runs tool commands in disposable containers with workspace-only mounts and no Docker socket in the tool container.

## Repository layout

```text
distributed-agent-system/
  orchestrator/
  node-agent/
  agent-server/
  tool-images/
  shared/
  deploy/
  tests/
  workspaces/
  logs/
  docs/
```


## Reproducible local development

Install all development dependencies from one file:

```bash
python -m pip install -r requirements-dev.txt
# or
make install
```

Run Docker-free unit tests:

```bash
pytest -q -m "not integration"
# or
make test
```

Run basic Docker Compose integration checks:

```bash
RUN_DOCKER_INTEGRATION_TESTS=1 pytest -q -m integration tests/integration
# or
make test-integration
```

Useful Make targets:

```bash
make install
make test
make lint
make compose-up
make compose-down
make build-tools
```

Unit tests do not require Docker, Postgres, Redis, or MinIO. Integration tests may require Docker Compose and the services defined in `deploy/docker-compose.control.yml`: Postgres, Redis, MinIO, orchestrator, node-agent, agent-server containers, and disposable tool images. Local compose-based tests should use `.env.test`. See `docs/step6.md` for details.

## Run with Docker Compose

From the repository root:

```bash
cp .env.example .env
# Edit HOST_WORKSPACE_ROOT to the absolute path of ./workspaces.
docker compose -f deploy/docker-compose.control.yml up --build
```

Then submit a task:

```bash
curl -sS -X POST http://localhost:8080/tasks \
  -H 'content-type: application/json' \
  -d '{"goal":"Create a short MVP result file.","priority":5,"workspace_id":"demo","mode":"normal"}' | jq
```

Check status:

```bash
curl -sS http://localhost:8080/tasks/<task_id> | jq
curl -sS http://localhost:8080/tasks/<task_id>/result | jq
```

The mock agent writes `workspaces/<workspace_id>/output/result.md` through a disposable tool container. Replace mock mode with an Ollama endpoint by setting `MOCK_AGENT=false` and `OLLAMA_URL=http://host.docker.internal:11434` on the agent-server services.

## Multi-machine node registration

Each node-agent can self-register with the orchestrator when `ORCHESTRATOR_URL` and `PUBLIC_NODE_URL` are set. The orchestrator exposes:

```text
POST /nodes/register
POST /nodes/{node_id}/heartbeat
GET /nodes
```

See `docs/phase2.md` for second-node setup and failure behavior, `docs/phase3.md` for delegation and model-class behavior, `docs/phase4.md` for the full tool plane, and `docs/phase5.md` for optimization, artifacts, memory, and monitoring.

## Local development without Docker child containers

For unit tests and quick development, the node-agent can fall back to host subprocess execution when Docker is unavailable:

```bash
NODE_AGENT_ALLOW_HOST_TOOL_FALLBACK=true pytest -q
```

Do not use that fallback for production-style isolation. It is a local test convenience only.

## Security boundary in this MVP

Tool containers receive:

- assigned workspace mount only
- read-only root filesystem
- `/tmp` tmpfs for scratch data
- non-root `1000:1000` runtime user
- CPU and memory limits
- timeout
- no privileged mode
- no host PID/IPC
- no Docker socket
- network disabled except for the internet tool type

The node-agent talks to Docker through the compose-managed `docker-socket-proxy` by default instead of mounting `/var/run/docker.sock` directly. This narrows the exposed Docker API surface, but Docker container creation still represents host-level authority; run node-agent only on hosts dedicated to this trust boundary. Tool containers do not receive Docker socket access.


## Step 5.4 hardening and observability

Step 5.4 adds shared `CLUSTER_TOKEN` authentication for orchestrator/node control traffic, stronger disposable Docker tool-container defaults, and a `/status` endpoint for cluster readiness checks. See `docs/step5.4.md` for operational details.

### Step 6.6

Step 6.6 adds explicit task-level success criteria and conservative final verification. See `docs/step6.6.md`.

## Database migrations and retention

The orchestrator uses Alembic for schema changes. Runtime `ALTER TABLE` patching has been removed. Apply migrations before starting services:

```bash
alembic upgrade head
```

The Docker Compose control stack runs migrations automatically before starting the API and worker. Retention is configurable with `AUDIT_LOG_RETENTION_DAYS`, `TOOL_RUN_RETENTION_DAYS`, and `TASK_RETENTION_DAYS`; run cleanup with `POST /admin/retention/run`. See `docs/step6.7.md` for migration, retention, and backup procedures.


### Step 6.8

Step 6.8 adds correlation IDs, structured audit levels/categories, duration fields, stable error codes, richer `/metrics`, and a protected human `/dashboard`. See `docs/step6.8.md`.

### Step 6.9

Step 6.9 adds node-agent heartbeat expiry recovery, dead-node job requeue, capability-aware scheduling, Docker/tool/platform checks, node draining mode, and node-agent version compatibility enforcement. See `docs/step6.9.md`.

### Step 6.10

Step 6.10 adds `/v1` API versioning, consistent error responses, pagination/filtering/sorting for externally consumed resources, idempotency keys for task submission, task cancellation, safe artifact downloads, and API output redaction. See `docs/step6.10.md`.


### Step 6.11

Step 6.11 consolidates duplicated tool runner code, versions tool images, adds centralized tool metadata and tool-specific policy enforcement, validates tool input/output schemas, and stores oversized full stdout/stderr as artifacts while returning truncated API summaries. See `docs/step6.11.md`.


## Step 6.13 - Memory system

Adds protected namespaced memory, provenance, confidence, retention/deletion, stale-update safeguards, and memory-read auditing. See `docs/step6.13.md`.
## Step 6.14 - Security hardening

Adds global secret redaction, restricted production CORS, request/response size limits, dependency and image scanning hooks, SBOM generation, pinned tool dependencies, non-root containers, and role-specific production credentials. See `docs/step6.14.md`.
## Step 6.15 - Documentation

Adds a clear architecture overview, task execution sequence diagram, node registration docs, deployment mode guidance, security assumptions, failure behavior, cleanup behavior, and troubleshooting guide. See `docs/architecture.md`, `docs/deployment.md`, `docs/operations.md`, and `docs/step6.15.md`.



## Step 6.16 - Code Quality

Adds Ruff linting and formatting, incremental MyPy/Pyright type checking configuration, stricter Pydantic model defaults, centralized lifecycle status names, smaller helper functions around task/job/tool completion paths, and docstrings for core lifecycle functions. See `docs/step6.16.md`.


## Step 6.17: deployment readiness

This version integrates production readiness into the real runtime. The orchestrator and node-agent expose `/readyz`, support draining/shutdown behavior, enforce node protocol compatibility, and include production Compose, persistent volume guidance, and backup/restore scripts. See `docs/step6.17-real-implementation.md`.


## step7.1 deployment gate

Run `scripts/validate-clean-checkout.sh` from a fresh checkout before staging. For production-like validation, run `scripts/smoke-prod-compose.sh` and then `RUN_DOCKER_INTEGRATION_TESTS=1 make test-integration`. Production Compose must use the `docker-socket-proxy`; direct `/var/run/docker.sock` mounts into `node-agent` or tool containers are forbidden. Configure distinct `NODE_TOKENS`, `OPERATOR_TOKENS`, `ADMIN_TOKENS`, and `READONLY_TOKENS`; use `DEPRECATED_TOKENS` only during a bounded rotation window.


## Step 7.1: deployment hardening

Adds production token role separation, Docker socket proxy enforcement for production Compose, Redis-backed rate limiting, active node-agent tool cancellation, stricter production secret validation, smoke/clean validation scripts, and deployment runbook/checklist updates. See `docs/step7.1-implementation-report.md`.
