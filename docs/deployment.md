# Deployment

## Local development

Install `requirements-dev.txt` in a virtual environment and run the Docker-free checks from the repository root. The default settings use local service addresses and the mock model backend. Host tool fallback is available only when `ENVIRONMENT=development` and `NODE_AGENT_ALLOW_HOST_TOOL_FALLBACK=true`.

## Single-machine deployment

`deploy/docker-compose.control.yml` starts the orchestrator API and worker, node agent, model servers, Docker socket proxy, Postgres, Redis, and MinIO. Set `HOST_WORKSPACE_ROOT` to the host directory that Docker will mount into tool containers. Use `.env.test` for local credentials; it is ignored by Git.

## Multi-node deployment

Run `deploy/docker-compose.node.yml` on each additional machine. Configure a unique `NODE_ID`, an externally reachable `PUBLIC_NODE_URL`, and the control-plane `ORCHESTRATOR_URL`. The node and orchestrator must share a supported protocol version and node token. Model URLs and tool image names can differ by node.

## Production deployment

`deploy/docker-compose.prod.yml` defines persistent volumes, health checks, resource limits, the socket proxy, and role-separated services. Run `scripts/validate-clean-checkout.sh` before staging and `scripts/smoke-prod-compose.sh` against a production-shaped environment.

Production requires explicit values for:

- `DATABASE_URL` with a non-default Postgres password
- `QUEUE_URL` with Redis authentication
- `NODE_TOKENS`, `OPERATOR_TOKENS`, `ADMIN_TOKENS`, and `READONLY_TOKENS`
- `CLUSTER_TOKEN` for compatibility paths, distinct from role tokens
- `CORS_ALLOW_ORIGINS` without `*`
- `HOST_WORKSPACE_ROOT` and persistent artifact storage

Request and response limits are controlled by `MAX_REQUEST_BODY_BYTES` and `MAX_RESPONSE_BODY_BYTES`. Apply Alembic migrations before starting an orchestrator outside the supplied Compose entrypoint.
