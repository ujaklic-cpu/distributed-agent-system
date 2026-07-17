# Operations

## Readiness and draining

Use `/readyz` on the orchestrator and each node agent for load-balancer checks. Before maintenance, call the node drain endpoint and wait for its running-job count to reach zero. Resume the node after Docker, model endpoints, and workspace mounts pass readiness checks.

## Backups and retention

The `scripts/backup-postgres.sh` and `scripts/restore-postgres.sh` scripts handle database dumps. The matching MinIO scripts copy object data. Test restores in an isolated environment before relying on a backup.

`POST /admin/retention/run` applies configured retention windows to audit logs, tool runs, tasks, and memory. Workspace and artifact files are separate from database retention; monitor and remove them under an operator-approved policy.

## Common failures

- Node not registering: verify `ORCHESTRATOR_URL`, `PUBLIC_NODE_URL`, protocol versions, role tokens, and network reachability.
- Task stuck running: inspect node heartbeats, job leases, `/v1/jobs`, and audit events for the task correlation ID.
- Docker unavailable: check the socket proxy and the node agent's `DOCKER_HOST`; do not mount the raw Docker socket into a tool container.
- Tool timeout: inspect the tool-run exit status and output excerpt, then adjust the command or the tool-specific limit if policy permits.
- Verification failure: use the recorded deterministic checks; non-empty result text is not proof of success.
- Postgres connection failure: confirm `DATABASE_URL`, credentials, migrations, and database readiness.
- Redis connection failure: confirm `QUEUE_URL`, authentication, and Redis health. Production does not silently fall back to in-memory rate limits.
- Artifact failure: confirm that `ARTIFACT_STORAGE_ROOT` is writable and has enough space.

Structured logs redact configured secret keys, bearer tokens, credential-bearing URLs, and common provider token formats. Raw request bodies should remain disabled. Use task IDs and correlation IDs to join orchestrator, node, tool, and verification events.
