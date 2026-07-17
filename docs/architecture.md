# Architecture

## Control flow

1. A client submits `POST /tasks` or `POST /v1/tasks` to the orchestrator.
2. The orchestrator writes task state to Postgres and queues the task ID in Redis.
3. The worker plans the task, selects a registered node, and reserves CPU, RAM, and GPU capacity.
4. The node agent calls the assigned model server or starts a disposable tool container.
5. Tool output returns through a versioned result schema. The orchestrator records verification evidence and copies declared output into the artifact store.

Postgres is the source of truth for tasks, jobs, nodes, audit events, memory, and artifact metadata. Redis holds queue and rate-limit state. MinIO is present in the Compose stack for object-storage integration, while the current artifact implementation writes under `ARTIFACT_STORAGE_ROOT`.

## Service boundaries

The orchestrator owns scheduling and policy decisions. Node agents report resources, accept assigned jobs, and manage container lifecycles. Model servers return text and structured tool requests; they do not start containers directly.

Tool containers receive `/workspace`, a read-only root filesystem, a non-root user, dropped Linux capabilities, and no Docker socket. Shell, test, build, and filesystem tools can write to the assigned workspace. Internet and analysis tools receive a read-only workspace plus a writable `.tool-results` mount for their result envelope.

## Node registration

A node sends `POST /nodes/register`, followed by periodic `POST /nodes/{node_id}/heartbeat` requests. Registration includes capabilities, supported tools, free resources, agent version, and protocol version. The scheduler rejects incompatible, stale, draining, or under-provisioned nodes.

Control traffic uses bearer tokens. `NODE_TOKENS` authenticate node traffic; `OPERATOR_TOKENS`, `ADMIN_TOKENS`, and `READONLY_TOKENS` separate API privileges. `CLUSTER_TOKEN` remains a development compatibility setting and cannot replace role tokens in production.

## Failure handling

Jobs use attempt IDs, leases, and generation numbers so late results cannot overwrite a newer attempt. Heartbeat expiry marks nodes unavailable and requeues eligible work. Task execution has configured bounds for attempts, model steps, repeated tool calls, and optimization rounds. A task completes only after its configured verification policy produces a terminal result.
