from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from sqlalchemy import func

from orchestrator.config import settings
from orchestrator.db import (
    ArtifactRecord,
    AuditLogRecord,
    JobRecord,
    MemoryRecord,
    NodeRecord,
    OptimizationRoundRecord,
    SessionLocal,
    TaskRecord,
    ToolRunRecord,
    init_db,
    run_retention_policy,
)
from orchestrator.lifecycle import RuntimeState, close_orchestrator_resources, orchestrator_readiness
from orchestrator.registry import (
    IncompatibleNodeVersion,
    list_nodes,
    mark_stale_nodes,
    node_to_dict,
    register_node,
    set_node_draining,
    update_heartbeat,
)
from orchestrator.task_queue import RedisTaskQueue
from shared.auth import TokenValidationResult, parse_token_list, token_hash_prefix, validate_role_token_list
from shared.logging import configure_logging, log_event
from shared.schemas import (
    ArtifactView,
    AuditLogView,
    JobView,
    MemoryCreate,
    MemoryDeleteResult,
    MemoryView,
    MetricsView,
    NodeHeartbeat,
    NodeRegister,
    NodeView,
    Page,
    PageMeta,
    TaskCancelResult,
    TaskCreate,
    TaskCreated,
    TaskResult,
    TaskStatus,
    TaskSuccessCriteria,
    TaskVerificationView,
    TaskView,
    ToolMetadataView,
    ToolRunView,
    new_id,
)
from shared.security import install_security_middleware, redact_json_string, redact_value
from shared.tool_metadata import default_tool_metadata

configure_logging(settings.log_level)
logger = logging.getLogger(__name__)

queue = RedisTaskQueue()
runtime_state = RuntimeState()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()
    runtime_state.resume_admission()
    log_event(logger, "orchestrator_started", category="lifecycle")
    try:
        yield
    finally:
        runtime_state.begin_shutdown()
        close_orchestrator_resources(queue)
        log_event(logger, "orchestrator_shutdown", category="lifecycle")


app = FastAPI(title="Distributed Agent Orchestrator", version="7.0", lifespan=lifespan)
install_security_middleware(
    app,
    environment=settings.environment,
    cors_allow_origins=settings.cors_allow_origins,
    max_request_body_bytes=settings.max_request_body_bytes,
    max_response_body_bytes=settings.max_response_body_bytes,
)


def _counts_by(session, model, column_name: str) -> dict[str, int]:
    column = getattr(model, column_name)
    rows = session.query(column, func.count()).group_by(column).all()
    return {str(key): int(count) for key, count in rows}


def _load_task_json(raw: str | None) -> dict[str, object]:
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _verification_status(details: dict[str, object]) -> str:
    return str(details.get("status") or "unverified")


def _verification_verified(details: dict[str, object]) -> bool | None:
    value = details.get("verified")
    if value is None:
        passed = details.get("passed")
        if passed is True:
            return True
        if passed is False:
            return False
        return None
    return bool(value)


def _task_to_view(task: TaskRecord) -> TaskView:
    verification = _load_task_json(task.verification_json)
    criteria = TaskSuccessCriteria.model_validate(_load_task_json(task.success_criteria_json))
    return TaskView(
        task_id=task.id,
        idempotency_key=task.idempotency_key,
        goal=task.goal,
        status=TaskStatus(task.status),
        priority=task.priority,
        mode=task.mode,
        workspace_id=task.workspace_id,
        attempts=task.attempts,
        max_attempts=task.max_attempts,
        current_phase=task.current_phase,
        assigned_nodes=json.loads(task.assigned_nodes or "[]"),
        error=task.error,
        error_code=task.error_code,
        correlation_id=task.correlation_id,
        duration_ms=task.duration_ms,
        created_at=task.created_at,
        updated_at=task.updated_at,
        completed=task.completed_at is not None
        or task.status
        in {
            TaskStatus.completed.value,
            TaskStatus.done.value,
            TaskStatus.verified.value,
            TaskStatus.unverified.value,
            TaskStatus.failed.value,
            TaskStatus.canceled.value,
        },
        verified=_verification_verified(verification),
        verification_status=_verification_status(verification),
        success_criteria=criteria,
        verification_details=TaskVerificationView.model_validate(verification) if verification else None,
    )


def _memory_to_view(row: MemoryRecord) -> MemoryView:
    return MemoryView(
        id=row.id,
        namespace=row.namespace,
        scope=row.scope,
        key=row.key,
        value=row.value,
        task_id=row.task_id,
        user_id=row.user_id,
        project_id=row.project_id,
        provenance=row.provenance,
        provenance_ref=row.provenance_ref,
        confidence=row.confidence,
        observed_at=row.observed_at,
        expires_at=row.expires_at,
        deleted_at=row.deleted_at,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _audit_memory_access(
    session,
    event: str,
    *,
    memory_id: str | None = None,
    namespace: str | None = None,
    scope: str | None = None,
    key: str | None = None,
    task_id: str | None = None,
    result_count: int | None = None,
    level: str = "info",
    error_code: str | None = None,
) -> None:
    payload = {
        "memory_id": memory_id,
        "namespace": namespace,
        "scope": scope,
        "key": key,
        "result_count": result_count,
    }
    session.add(
        AuditLogRecord(
            event=event,
            level=level,
            category="memory",
            task_id=task_id,
            error_code=error_code,
            payload=json.dumps({k: v for k, v in payload.items() if v is not None}, default=str),
        )
    )
    log_event(logger, event, level=level, category="memory", task_id=task_id, error_code=error_code, **payload)


def _api_error(status_code: int, code: str, message: str, details: dict[str, Any] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content={"error": {"code": code, "message": message, "details": details or {}}}
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        return JSONResponse(status_code=exc.status_code, content=detail)
    code = "HTTP_ERROR"
    message = str(detail)
    if exc.status_code == 400:
        code = "BAD_REQUEST"
    elif exc.status_code == 401:
        code = "UNAUTHORIZED"
    elif exc.status_code == 403:
        code = "FORBIDDEN"
    elif exc.status_code == 404:
        code = "NOT_FOUND"
    elif exc.status_code == 409:
        code = "CONFLICT"
    elif exc.status_code == 422:
        code = "VALIDATION_ERROR"
    return _api_error(exc.status_code, code, message)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return _api_error(422, "VALIDATION_ERROR", "Request validation failed", {"errors": exc.errors()})


def _raise_api(status_code: int, code: str, message: str, details: dict[str, Any] | None = None) -> None:
    raise HTTPException(
        status_code=status_code, detail={"error": {"code": code, "message": message, "details": details or {}}}
    )


def _page(query, limit: int, offset: int):
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    total = int(query.count())
    rows = query.offset(offset).limit(limit).all()
    next_offset = offset + limit if offset + limit < total else None
    return rows, PageMeta(limit=limit, offset=offset, total=total, next_offset=next_offset)


def _apply_time_range(query, model, created_after: datetime | None, created_before: datetime | None):
    column = getattr(model, "created_at", None)
    if column is None:
        column = getattr(model, "started_at", None)
    if created_after is not None and column is not None:
        query = query.filter(column >= created_after)
    if created_before is not None and column is not None:
        query = query.filter(column <= created_before)
    return query


def _sort(query, model, sort: str | None, allowed: dict[str, object], default_field: str, default_desc: bool = True):
    sort = sort or (f"-{default_field}" if default_desc else default_field)
    descending = sort.startswith("-")
    field = sort[1:] if descending else sort
    if field not in allowed:
        _raise_api(400, "INVALID_SORT", f"Unsupported sort field: {field}", {"allowed": sorted(allowed)})
    column = allowed[field]
    return query.order_by(column.desc() if descending else column.asc())


def _redact_text(value: str | None, replacement: str = "[redacted]") -> str:
    return replacement if value else ""


def _redact_payload(value: Any) -> Any:
    return redact_value(value)


def _safe_json(raw: str | None, default: Any) -> Any:
    return redact_json_string(raw, default)


def _artifact_to_view(row: ArtifactRecord) -> ArtifactView:
    return ArtifactView(
        id=row.id,
        task_id=row.task_id,
        workspace_id=row.workspace_id,
        name=row.name,
        source_path="[redacted]",
        uri=row.uri,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        created_at=row.created_at,
    )


def _tool_run_to_view(row: ToolRunRecord) -> ToolRunView:
    return ToolRunView(
        id=row.id,
        task_id=row.task_id,
        subtask_id=row.subtask_id,
        node_id=row.node_id,
        tool_type=row.tool_type,
        command="[redacted]",
        network=bool(row.network),
        limits={
            "cpus": row.cpus,
            "memory": row.memory,
            "timeout_seconds": row.timeout_seconds,
            "output_limit_mb": row.output_limit_mb,
        },
        status=row.status,
        exit_code=row.exit_code,
        stdout_excerpt=row.stdout_excerpt or "",
        stderr_excerpt=row.stderr_excerpt or "",
        error=row.error,
        error_code=row.error_code,
        correlation_id=row.correlation_id,
        duration_ms=row.duration_ms or 0,
    )


def _job_to_view(row: JobRecord) -> JobView:
    return JobView(
        id=row.id,
        task_id=row.task_id,
        subtask_id=row.subtask_id,
        job_type=row.job_type,
        status=row.status,
        node_id=row.assigned_node_id,
        started_at=row.started_at,
        finished_at=row.finished_at,
        lost_at=row.lost_at,
        attempt_id=row.attempt_id,
        lease_expires_at=row.lease_expires_at,
        generation=row.generation,
        error_code=row.error_code,
        correlation_id=row.correlation_id,
        duration_ms=row.duration_ms or 0,
        created_at=getattr(row, "created_at", None),
        updated_at=getattr(row, "updated_at", None),
    )


def _audit_security_event(
    event: str,
    *,
    request: Request | None = None,
    role_required: str | None = None,
    token_hash_value: str | None = None,
    level: str = "warning",
    error_code: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    payload = {
        "role_required": role_required,
        "endpoint": str(request.url.path) if request is not None else None,
        "remote_addr_hash": token_hash_prefix(request.client.host) if request is not None and request.client else None,
        "token_hash_prefix": token_hash_value[:12] if token_hash_value else None,
        **(details or {}),
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    correlation_id = request.headers.get("x-correlation-id") if request is not None else None
    log_event(
        logger, event, level=level, category="auth", correlation_id=correlation_id, error_code=error_code, **payload
    )
    try:
        with SessionLocal() as session:
            session.add(
                AuditLogRecord(
                    event=event,
                    level=level,
                    category="auth",
                    correlation_id=correlation_id,
                    error_code=error_code,
                    payload=json.dumps(payload, default=str),
                )
            )
            session.commit()
    except Exception:
        logger.debug("security audit persistence skipped", exc_info=True)


def _auth_for_role(role: str, authorization: str | None, request: Request | None = None) -> TokenValidationResult:
    try:
        result = validate_role_token_list(
            authorization,
            settings.tokens_for_role(role),
            realm=role,
            deprecated_tokens=parse_token_list(settings.deprecated_tokens),
            allow_empty=settings.environment != "production",
        )
    except HTTPException as exc:
        event = "token_missing" if exc.status_code == 401 else "token_rejected"
        _audit_security_event(event, request=request, role_required=role, level="warning", error_code="AUTH_FAILED")
        raise
    if result.state == "deprecated":
        _audit_security_event(
            "deprecated_token_used",
            request=request,
            role_required=role,
            token_hash_value=result.token_hash,
            level="warning",
            error_code="DEPRECATED_TOKEN",
        )
    return result


def _auth_for_any_role(
    roles: list[str], authorization: str | None, request: Request | None = None
) -> TokenValidationResult:
    last_exc: HTTPException | None = None
    for role in roles:
        try:
            return _auth_for_role(role, authorization, request)
        except HTTPException as exc:
            last_exc = exc
            if exc.status_code == 401:
                break
    _audit_security_event(
        "wrong_role_token",
        request=request,
        role_required="|".join(roles),
        level="warning",
        error_code="WRONG_ROLE",
    )
    raise last_exc or HTTPException(status_code=403, detail="invalid role token")


_rate_limiter = None


def _get_rate_limiter():
    global _rate_limiter
    if _rate_limiter is not None:
        return _rate_limiter
    from shared.rate_limit import InMemoryRateLimiter, RedisRateLimiter

    try:
        _rate_limiter = RedisRateLimiter(settings.queue_url)
    except Exception as err:
        if settings.environment == "production" and not settings.allow_in_memory_rate_limit_fallback:
            raise HTTPException(status_code=503, detail="Redis rate limiter is unavailable") from err
        _rate_limiter = InMemoryRateLimiter()
    return _rate_limiter


def _apply_rate_limit(
    request: Request, context: TokenValidationResult, bucket: str, limit: int, identity: str | None = None
) -> None:
    limiter = _get_rate_limiter()
    entity = identity or context.token_hash or (request.client.host if request.client else "unknown")
    decision = limiter.check(f"{bucket}:{entity}", limit, 60)
    if not decision.allowed:
        _audit_security_event(
            "rate_limit_exceeded",
            request=request,
            role_required=context.role,
            token_hash_value=context.token_hash,
            level="warning",
            error_code="RATE_LIMITED",
            details={"bucket": bucket, "limit": limit},
        )
        raise HTTPException(
            status_code=429,
            detail={
                "error": {
                    "code": "RATE_LIMITED",
                    "message": "Rate limit exceeded",
                    "details": {"bucket": bucket, "limit": limit, "reset_after_seconds": decision.reset_after_seconds},
                }
            },
        )


def rate_limit_task_submission(request: Request, authorization: str | None = Header(default=None)) -> None:
    _apply_rate_limit(request, _auth_for_role("operator", authorization, request), "task_submission", 30)


def rate_limit_node_registration(request: Request, authorization: str | None = Header(default=None)) -> None:
    context = _auth_for_role("node", authorization, request)
    identity = context.token_hash or (request.client.host if request.client else "unknown")
    _apply_rate_limit(request, context, "node_registration", 10, identity)


def rate_limit_heartbeat(node_id: str, request: Request, authorization: str | None = Header(default=None)) -> None:
    context = _auth_for_role("node", authorization, request)
    _apply_rate_limit(request, context, "heartbeat", 120, node_id)


def rate_limit_memory_write(request: Request, authorization: str | None = Header(default=None)) -> None:
    _apply_rate_limit(request, _auth_for_role("admin", authorization, request), "memory_write", 30)


def rate_limit_audit_access(request: Request, authorization: str | None = Header(default=None)) -> None:
    context = _auth_for_any_role(["operator", "admin"], authorization, request)
    _apply_rate_limit(request, context, "audit_access", 60)


def rate_limit_observability(request: Request, authorization: str | None = Header(default=None)) -> None:
    context = _auth_for_any_role(["readonly", "operator", "admin"], authorization, request)
    _apply_rate_limit(request, context, "observability", 120)


def require_node_auth(request: Request, authorization: str | None = Header(default=None)) -> TokenValidationResult:
    return _auth_for_role("node", authorization, request)


def require_operator_auth(request: Request, authorization: str | None = Header(default=None)) -> TokenValidationResult:
    return _auth_for_role("operator", authorization, request)


def require_admin_auth(request: Request, authorization: str | None = Header(default=None)) -> TokenValidationResult:
    return _auth_for_role("admin", authorization, request)


def require_operator_or_node_auth(
    request: Request, authorization: str | None = Header(default=None)
) -> TokenValidationResult:
    return _auth_for_any_role(["operator", "node"], authorization, request)


def require_observability_auth(
    request: Request, authorization: str | None = Header(default=None)
) -> TokenValidationResult:
    return _auth_for_any_role(["readonly", "operator", "admin"], authorization, request)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> JSONResponse:
    ready, payload = orchestrator_readiness(runtime_state)
    return JSONResponse(status_code=200 if ready else 503, content=payload)


@app.post("/tasks", response_model=TaskCreated, dependencies=[Depends(rate_limit_task_submission)])
@app.post("/v1/tasks", response_model=TaskCreated, dependencies=[Depends(rate_limit_task_submission)])
def submit_task(
    payload: TaskCreate, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")
) -> TaskCreated:
    if not runtime_state.snapshot()["accepting_jobs"]:
        _raise_api(503, "ORCHESTRATOR_DRAINING", "Orchestrator is draining and not accepting new tasks")
    task_id = new_id("task")
    with SessionLocal() as session:
        if idempotency_key:
            existing = session.query(TaskRecord).filter(TaskRecord.idempotency_key == idempotency_key).one_or_none()
            if existing is not None:
                return TaskCreated(
                    task_id=existing.id, status=TaskStatus(existing.status), idempotency_key=existing.idempotency_key
                )
        correlation_id = new_id("corr")
        task = TaskRecord(
            id=task_id,
            correlation_id=correlation_id,
            goal=payload.goal,
            status=TaskStatus.queued.value,
            priority=payload.priority,
            workspace_id=payload.workspace_id,
            mode=payload.mode.value,
            created_by=payload.created_by,
            idempotency_key=idempotency_key,
            max_attempts=settings.max_task_attempts,
            success_criteria_json=payload.success_criteria.model_dump_json(),
            verification_json=json.dumps(
                {
                    "status": "unverified",
                    "passed": None,
                    "verified": None,
                    "explanation": "task has not completed execution yet",
                    "evidence": {},
                }
            ),
        )
        session.add(task)
        session.commit()
    queue.enqueue(task_id, priority=payload.priority)
    log_event(
        logger,
        "task_created",
        category="scheduler",
        task_id=task_id,
        correlation_id=correlation_id,
        workspace_id=payload.workspace_id,
        priority=payload.priority,
    )
    return TaskCreated(task_id=task_id, status=TaskStatus.queued, idempotency_key=idempotency_key)


@app.get("/tasks/{task_id}", response_model=TaskView, dependencies=[Depends(require_operator_auth)])
@app.get("/v1/tasks/{task_id}", response_model=TaskView, dependencies=[Depends(require_operator_auth)])
def get_task(task_id: str) -> TaskView:
    with SessionLocal() as session:
        task = session.get(TaskRecord, task_id)
        if task is None:
            _raise_api(404, "TASK_NOT_FOUND", "Task not found", {"task_id": task_id})
        return _task_to_view(task)


@app.get("/tasks/{task_id}/result", response_model=TaskResult, dependencies=[Depends(require_operator_auth)])
@app.get("/v1/tasks/{task_id}/result", response_model=TaskResult, dependencies=[Depends(require_operator_auth)])
def get_task_result(task_id: str) -> TaskResult:
    with SessionLocal() as session:
        task = session.get(TaskRecord, task_id)
        if task is None:
            _raise_api(404, "TASK_NOT_FOUND", "Task not found", {"task_id": task_id})
        verification = _load_task_json(task.verification_json)
        criteria = TaskSuccessCriteria.model_validate(_load_task_json(task.success_criteria_json))
        return TaskResult(
            task_id=task.id,
            idempotency_key=task.idempotency_key,
            status=TaskStatus(task.status),
            result=task.result,
            artifacts=_safe_json(task.artifacts_json, []),
            error=task.error,
            completed=task.completed_at is not None
            or task.status
            in {
                TaskStatus.completed.value,
                TaskStatus.done.value,
                TaskStatus.verified.value,
                TaskStatus.unverified.value,
                TaskStatus.failed.value,
                TaskStatus.canceled.value,
            },
            verified=_verification_verified(verification),
            verification_status=_verification_status(verification),
            success_criteria=criteria,
            verification_details=TaskVerificationView.model_validate(verification) if verification else None,
        )


@app.get("/tasks/{task_id}/tools", response_model=list[ToolRunView], dependencies=[Depends(require_operator_auth)])
@app.get("/v1/tasks/{task_id}/tools", response_model=list[ToolRunView], dependencies=[Depends(require_operator_auth)])
def list_task_tool_runs(task_id: str) -> list[ToolRunView]:
    with SessionLocal() as session:
        task = session.get(TaskRecord, task_id)
        if task is None:
            _raise_api(404, "TASK_NOT_FOUND", "Task not found", {"task_id": task_id})
        rows = session.query(ToolRunRecord).filter(ToolRunRecord.task_id == task_id).order_by(ToolRunRecord.id).all()
        return [_tool_run_to_view(row) for row in rows]


@app.get("/tasks/{task_id}/artifacts", response_model=list[ArtifactView], dependencies=[Depends(require_operator_auth)])
@app.get(
    "/v1/tasks/{task_id}/artifacts", response_model=list[ArtifactView], dependencies=[Depends(require_operator_auth)]
)
def list_task_artifacts(task_id: str) -> list[ArtifactView]:
    with SessionLocal() as session:
        task = session.get(TaskRecord, task_id)
        if task is None:
            _raise_api(404, "TASK_NOT_FOUND", "Task not found", {"task_id": task_id})
        rows = (
            session.query(ArtifactRecord).filter(ArtifactRecord.task_id == task_id).order_by(ArtifactRecord.name).all()
        )
        return [_artifact_to_view(row) for row in rows]


@app.post("/nodes/register", response_model=NodeView, dependencies=[Depends(rate_limit_node_registration)])
def register_node_endpoint(payload: NodeRegister, request: Request) -> NodeView:
    base_url = payload.base_url or settings.node_agent_url
    try:
        node = register_node(payload, base_url)
    except IncompatibleNodeVersion as exc:
        log_event(
            logger,
            "node_version_rejected",
            level="warning",
            category="node",
            node_id=payload.node_id,
            error_code="NODE_VERSION_INCOMPATIBLE",
            agent_version=payload.agent_version,
            protocol_version=payload.protocol_version,
        )
        _raise_api(
            409,
            "NODE_VERSION_INCOMPATIBLE",
            str(exc),
            {
                "node_id": payload.node_id,
                "agent_version": payload.agent_version,
                "protocol_version": payload.protocol_version,
            },
        )
    log_event(
        logger,
        "node_registered",
        category="node",
        node_id=node.id,
        base_url=node.base_url,
        agent_version=node.agent_version,
        protocol_version=getattr(node, "protocol_version", "unknown"),
    )
    return NodeView(**node_to_dict(node))


@app.post("/nodes/{node_id}/heartbeat", response_model=NodeView, dependencies=[Depends(rate_limit_heartbeat)])
def node_heartbeat_endpoint(node_id: str, payload: NodeHeartbeat) -> NodeView:
    node = update_heartbeat(node_id, payload)
    if node is None:
        _raise_api(404, "NODE_NOT_REGISTERED", "Node not registered", {"node_id": node_id})
    log_event(
        logger,
        "node_heartbeat",
        category="node",
        node_id=node.id,
        status=node.status,
        running_jobs=node.running_jobs,
        agent_version=node.agent_version,
    )
    if node.status == "incompatible":
        _raise_api(
            409, "NODE_VERSION_INCOMPATIBLE", "Node-agent version or protocol is incompatible", {"node_id": node_id}
        )
    return NodeView(**node_to_dict(node))


@app.post("/nodes/{node_id}/drain", response_model=NodeView, dependencies=[Depends(require_operator_or_node_auth)])
def drain_node_endpoint(node_id: str) -> NodeView:
    node = set_node_draining(node_id, True)
    if node is None:
        _raise_api(404, "NODE_NOT_FOUND", "Node not found", {"node_id": node_id})
    log_event(logger, "node_draining_enabled", category="node", node_id=node.id)
    return NodeView(**node_to_dict(node))


@app.post("/nodes/{node_id}/resume", response_model=NodeView, dependencies=[Depends(require_operator_or_node_auth)])
def resume_node_endpoint(node_id: str) -> NodeView:
    node = set_node_draining(node_id, False)
    if node is None:
        _raise_api(404, "NODE_NOT_FOUND", "Node not found", {"node_id": node_id})
    log_event(logger, "node_draining_disabled", category="node", node_id=node.id)
    return NodeView(**node_to_dict(node))


@app.get("/nodes", response_model=list[NodeView], dependencies=[Depends(require_operator_auth)])
@app.get("/v1/nodes", response_model=list[NodeView], dependencies=[Depends(require_operator_auth)])
def list_nodes_endpoint() -> list[NodeView]:
    mark_stale_nodes()
    return [NodeView(**node_to_dict(node)) for node in list_nodes()]


@app.get("/v1/tools", response_model=list[ToolMetadataView], dependencies=[Depends(require_operator_auth)])
def list_tools_v1() -> list[ToolMetadataView]:
    return [ToolMetadataView(**metadata.as_dict()) for metadata in default_tool_metadata().values()]


@app.post("/memory", response_model=MemoryView, dependencies=[Depends(rate_limit_memory_write)])
@app.post("/v1/memory", response_model=MemoryView, dependencies=[Depends(rate_limit_memory_write)])
def upsert_memory(payload: MemoryCreate) -> MemoryView:
    now = datetime.now(UTC)
    observed_at = payload.observed_at or now
    with SessionLocal() as session:
        row = (
            session.query(MemoryRecord)
            .filter(
                MemoryRecord.namespace == payload.namespace.value,
                MemoryRecord.scope == payload.scope,
                MemoryRecord.key == payload.key,
            )
            .one_or_none()
        )
        if row is not None and not payload.allow_stale_override:
            existing_observed_at = row.observed_at
            if existing_observed_at.tzinfo is None:
                existing_observed_at = existing_observed_at.replace(tzinfo=UTC)
            incoming_observed_at = observed_at
            if incoming_observed_at.tzinfo is None:
                incoming_observed_at = incoming_observed_at.replace(tzinfo=UTC)
            if incoming_observed_at < existing_observed_at:
                _audit_memory_access(
                    session,
                    "memory_stale_update_rejected",
                    memory_id=row.id,
                    namespace=row.namespace,
                    scope=row.scope,
                    key=row.key,
                    task_id=payload.task_id or row.task_id,
                    level="warning",
                    error_code="STALE_MEMORY_UPDATE",
                )
                session.commit()
                _raise_api(
                    409, "STALE_MEMORY_UPDATE", "Incoming memory is older than the stored fact", {"memory_id": row.id}
                )
        if row is None:
            row = MemoryRecord(
                id=new_id("memory"),
                namespace=payload.namespace.value,
                scope=payload.scope,
                key=payload.key,
                value=payload.value,
                task_id=payload.task_id,
                user_id=payload.user_id,
                project_id=payload.project_id,
                provenance=payload.provenance.value,
                provenance_ref=payload.provenance_ref,
                confidence=payload.confidence,
                observed_at=observed_at,
                expires_at=payload.expires_at,
            )
            session.add(row)
            event = "memory_created"
        else:
            row.value = payload.value
            row.task_id = payload.task_id
            row.user_id = payload.user_id
            row.project_id = payload.project_id
            row.provenance = payload.provenance.value
            row.provenance_ref = payload.provenance_ref
            row.confidence = payload.confidence
            row.observed_at = observed_at
            row.expires_at = payload.expires_at
            row.deleted_at = None
            row.version += 1
            event = "memory_updated"
        session.flush()
        _audit_memory_access(
            session,
            event,
            memory_id=row.id,
            namespace=row.namespace,
            scope=row.scope,
            key=row.key,
            task_id=row.task_id,
            result_count=1,
        )
        session.commit()
        session.refresh(row)
        return _memory_to_view(row)


@app.get("/memory", response_model=list[MemoryView], dependencies=[Depends(require_admin_auth)])
@app.get("/v1/memory", response_model=list[MemoryView], dependencies=[Depends(require_admin_auth)])
def list_memory(
    namespace: str | None = None,
    scope: str | None = None,
    task_id: str | None = None,
    user_id: str | None = None,
    project_id: str | None = None,
    key: str | None = None,
    include_expired: bool = False,
) -> list[MemoryView]:
    now = datetime.now(UTC)
    with SessionLocal() as session:
        query = session.query(MemoryRecord).filter(MemoryRecord.deleted_at.is_(None))
        if not include_expired:
            query = query.filter((MemoryRecord.expires_at.is_(None)) | (MemoryRecord.expires_at >= now))
        if namespace:
            query = query.filter(MemoryRecord.namespace == namespace)
        if scope:
            query = query.filter(MemoryRecord.scope == scope)
        if task_id:
            query = query.filter(MemoryRecord.task_id == task_id)
        if user_id:
            query = query.filter(MemoryRecord.user_id == user_id)
        if project_id:
            query = query.filter(MemoryRecord.project_id == project_id)
        if key:
            query = query.filter(MemoryRecord.key == key)
        rows = query.order_by(MemoryRecord.namespace, MemoryRecord.scope, MemoryRecord.key).all()
        _audit_memory_access(
            session,
            "memory_read",
            namespace=namespace,
            scope=scope,
            key=key,
            task_id=task_id,
            result_count=len(rows),
        )
        session.commit()
        return [_memory_to_view(row) for row in rows]


@app.delete("/memory/{memory_id}", response_model=MemoryDeleteResult, dependencies=[Depends(require_admin_auth)])
@app.delete("/v1/memory/{memory_id}", response_model=MemoryDeleteResult, dependencies=[Depends(require_admin_auth)])
def delete_memory_v1(memory_id: str) -> MemoryDeleteResult:
    now = datetime.now(UTC)
    with SessionLocal() as session:
        row = session.get(MemoryRecord, memory_id)
        if row is None or row.deleted_at is not None:
            _raise_api(404, "MEMORY_NOT_FOUND", "Memory not found", {"memory_id": memory_id})
        row.deleted_at = now
        row.version += 1
        _audit_memory_access(
            session,
            "memory_deleted",
            memory_id=row.id,
            namespace=row.namespace,
            scope=row.scope,
            key=row.key,
            task_id=row.task_id,
            result_count=1,
        )
        session.commit()
        return MemoryDeleteResult(id=memory_id, deleted=True)


@app.get("/metrics", response_model=MetricsView, dependencies=[Depends(rate_limit_observability)])
def metrics() -> MetricsView:
    mark_stale_nodes()
    try:
        queue_depth = queue.depth()
    except Exception:
        queue_depth = 0
    with SessionLocal() as session:
        jobs = _counts_by(session, JobRecord, "status")
        nodes = _counts_by(session, NodeRecord, "status")
        avg_duration = session.query(func.avg(JobRecord.duration_ms)).filter(JobRecord.duration_ms > 0).scalar() or 0
        return MetricsView(
            tasks=_counts_by(session, TaskRecord, "status"),
            nodes=nodes,
            jobs=jobs,
            tool_runs=_counts_by(session, ToolRunRecord, "status"),
            optimization_rounds=session.query(OptimizationRoundRecord).count(),
            artifacts=session.query(ArtifactRecord).count(),
            memories=session.query(MemoryRecord).count(),
            queue_depth=queue_depth,
            running_jobs=int(jobs.get("running", 0)),
            failed_jobs=int(jobs.get("failed", 0) + jobs.get("lost", 0)),
            completed_jobs=int(jobs.get("done", 0)),
            node_availability={
                "healthy": int(nodes.get("healthy", 0)),
                "unreachable": int(nodes.get("unreachable", 0)),
                "dead": int(nodes.get("dead", 0)),
                "recovered": int(nodes.get("recovered", 0)),
                "draining": int(nodes.get("draining", 0)),
                "incompatible": int(nodes.get("incompatible", 0)),
            },
            average_job_duration_ms=float(avg_duration),
            timeout_count=session.query(ToolRunRecord).filter(ToolRunRecord.status == "timeout").count(),
            retry_count=max(
                0, int((session.query(func.sum(TaskRecord.attempts)).scalar() or 0) - session.query(TaskRecord).count())
            ),
        )


@app.get("/audit", response_model=list[AuditLogView], dependencies=[Depends(rate_limit_audit_access)])
def list_audit_logs(task_id: str | None = None, limit: int = 100) -> list[AuditLogView]:
    limit = max(1, min(limit, 500))
    with SessionLocal() as session:
        query = session.query(AuditLogRecord)
        if task_id:
            query = query.filter(AuditLogRecord.task_id == task_id)
        rows = query.order_by(AuditLogRecord.id.desc()).limit(limit).all()
        return [
            AuditLogView(
                id=row.id,
                event=row.event,
                level=row.level,
                category=row.category,
                correlation_id=row.correlation_id,
                task_id=row.task_id,
                subtask_id=row.subtask_id,
                job_id=row.job_id,
                node_id=row.node_id,
                tool_run_id=row.tool_run_id,
                artifact_id=row.artifact_id,
                duration_ms=row.duration_ms or 0,
                error_code=row.error_code,
                payload=_safe_json(row.payload, {}),
                created_at=row.created_at,
            )
            for row in rows
        ]


@app.get("/v1/tasks", response_model=Page[TaskView], dependencies=[Depends(require_operator_auth)])
def list_tasks_v1(
    status: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    failure_reason: str | None = None,
    sort: str | None = Query(default="-created_at"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Page[TaskView]:
    with SessionLocal() as session:
        query = session.query(TaskRecord)
        if status:
            query = query.filter(TaskRecord.status == status)
        if failure_reason:
            query = query.filter(
                (TaskRecord.error_code == failure_reason) | (TaskRecord.error.contains(failure_reason))
            )
        query = _apply_time_range(query, TaskRecord, created_after, created_before)
        query = _sort(
            query,
            TaskRecord,
            sort,
            {
                "created_at": TaskRecord.created_at,
                "updated_at": TaskRecord.updated_at,
                "status": TaskRecord.status,
                "priority": TaskRecord.priority,
            },
            "created_at",
        )
        rows, meta = _page(query, limit, offset)
        return Page(items=[_task_to_view(row) for row in rows], page=meta)


@app.post("/v1/tasks/{task_id}/cancel", response_model=TaskCancelResult, dependencies=[Depends(require_operator_auth)])
def cancel_task_v1(task_id: str) -> TaskCancelResult:
    terminal = {"completed", "done", "verified", "unverified", "failed", "canceled"}
    now = datetime.now(UTC)
    with SessionLocal() as session:
        task = session.get(TaskRecord, task_id)
        if task is None:
            _raise_api(404, "TASK_NOT_FOUND", "Task not found", {"task_id": task_id})
        if task.status not in terminal:
            task.status = "canceled"
            task.error_code = "TASK_CANCELED"
            task.error = "Task canceled by operator"
            task.completed_at = now
        jobs = (
            session.query(JobRecord)
            .filter(JobRecord.task_id == task_id, JobRecord.status.in_(["queued", "running"]))
            .all()
        )
        cancel_targets: list[tuple[str, str]] = []
        for job in jobs:
            if job.status == "running" and job.assigned_node_id:
                node = session.get(NodeRecord, job.assigned_node_id)
                if node and node.base_url:
                    cancel_targets.append((job.id, node.base_url))
            job.status = "lost" if job.status == "running" else "failed"
            job.error_code = "TASK_CANCELED"
            job.finished_at = now
            job.duration_ms = job.duration_ms or 0
        session.commit()
    node_token = (parse_token_list(settings.node_tokens) or [settings.cluster_token])[0]
    for job_id, base_url in cancel_targets:
        try:
            with httpx.Client(timeout=5) as client:
                client.post(
                    f"{base_url.rstrip('/')}/jobs/{job_id}/cancel",
                    headers={"Authorization": f"Bearer {node_token}"} if node_token else {},
                )
        except Exception as exc:
            log_event(
                logger,
                "job_cancel_notify_failed",
                level="warning",
                category="scheduler",
                task_id=task_id,
                job_id=job_id,
                error=str(exc),
            )
    log_event(
        logger,
        "task_canceled",
        category="scheduler",
        task_id=task_id,
        error_code="TASK_CANCELED",
        canceled_jobs=len(jobs),
    )
    return TaskCancelResult(task_id=task_id, status=TaskStatus(task.status), canceled_jobs=len(jobs))


@app.get("/v1/jobs", response_model=Page[JobView], dependencies=[Depends(require_operator_auth)])
def list_jobs_v1(
    status: str | None = None,
    task_id: str | None = None,
    node_id: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    failure_reason: str | None = None,
    sort: str | None = Query(default="-created_at"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Page[JobView]:
    with SessionLocal() as session:
        query = session.query(JobRecord)
        if status:
            query = query.filter(JobRecord.status == status)
        if task_id:
            query = query.filter(JobRecord.task_id == task_id)
        if node_id:
            query = query.filter(JobRecord.assigned_node_id == node_id)
        if failure_reason:
            query = query.filter(JobRecord.error_code == failure_reason)
        query = _apply_time_range(query, JobRecord, created_after, created_before)
        query = _sort(
            query,
            JobRecord,
            sort,
            {
                "created_at": JobRecord.created_at,
                "updated_at": JobRecord.updated_at,
                "status": JobRecord.status,
                "started_at": JobRecord.started_at,
            },
            "created_at",
        )
        rows, meta = _page(query, limit, offset)
        return Page(items=[_job_to_view(row) for row in rows], page=meta)


@app.get("/v1/tool-runs", response_model=Page[ToolRunView], dependencies=[Depends(require_operator_auth)])
def list_tool_runs_v1(
    status: str | None = None,
    task_id: str | None = None,
    node_id: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    failure_reason: str | None = None,
    sort: str | None = Query(default="-started_at"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Page[ToolRunView]:
    with SessionLocal() as session:
        query = session.query(ToolRunRecord)
        if status:
            query = query.filter(ToolRunRecord.status == status)
        if task_id:
            query = query.filter(ToolRunRecord.task_id == task_id)
        if node_id:
            query = query.filter(ToolRunRecord.node_id == node_id)
        if failure_reason:
            query = query.filter(
                (ToolRunRecord.error_code == failure_reason) | (ToolRunRecord.error.contains(failure_reason))
            )
        if created_after:
            query = query.filter(ToolRunRecord.started_at >= created_after)
        if created_before:
            query = query.filter(ToolRunRecord.started_at <= created_before)
        query = _sort(
            query,
            ToolRunRecord,
            sort,
            {
                "started_at": ToolRunRecord.started_at,
                "finished_at": ToolRunRecord.finished_at,
                "status": ToolRunRecord.status,
            },
            "started_at",
        )
        rows, meta = _page(query, limit, offset)
        return Page(items=[_tool_run_to_view(row) for row in rows], page=meta)


@app.get("/v1/artifacts", response_model=Page[ArtifactView], dependencies=[Depends(require_operator_auth)])
def list_artifacts_v1(
    task_id: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    sort: str | None = Query(default="-created_at"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Page[ArtifactView]:
    with SessionLocal() as session:
        query = session.query(ArtifactRecord)
        if task_id:
            query = query.filter(ArtifactRecord.task_id == task_id)
        query = _apply_time_range(query, ArtifactRecord, created_after, created_before)
        query = _sort(
            query,
            ArtifactRecord,
            sort,
            {"created_at": ArtifactRecord.created_at, "name": ArtifactRecord.name},
            "created_at",
        )
        rows, meta = _page(query, limit, offset)
        return Page(items=[_artifact_to_view(row) for row in rows], page=meta)


@app.get("/v1/artifacts/{artifact_id}/download", dependencies=[Depends(require_operator_auth)])
def download_artifact_v1(artifact_id: str) -> FileResponse:
    with SessionLocal() as session:
        artifact = session.get(ArtifactRecord, artifact_id)
        if artifact is None:
            _raise_api(404, "ARTIFACT_NOT_FOUND", "Artifact not found", {"artifact_id": artifact_id})
        storage_root = Path(settings.artifact_storage_root).resolve()
        stored_path = Path(artifact.stored_path).resolve()
        if storage_root not in stored_path.parents and stored_path != storage_root:
            _raise_api(403, "ARTIFACT_PATH_FORBIDDEN", "Artifact path is outside the configured artifact store")
        if not stored_path.is_file():
            _raise_api(404, "ARTIFACT_FILE_NOT_FOUND", "Artifact file not found", {"artifact_id": artifact_id})
        return FileResponse(path=str(stored_path), filename=Path(artifact.name).name)


@app.get("/v1/audit", response_model=Page[AuditLogView], dependencies=[Depends(rate_limit_audit_access)])
def list_audit_logs_v1(
    task_id: str | None = None,
    node_id: str | None = None,
    status: str | None = None,
    category: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    failure_reason: str | None = None,
    sort: str | None = Query(default="-created_at"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Page[AuditLogView]:
    with SessionLocal() as session:
        query = session.query(AuditLogRecord)
        if task_id:
            query = query.filter(AuditLogRecord.task_id == task_id)
        if node_id:
            query = query.filter(AuditLogRecord.node_id == node_id)
        if category:
            query = query.filter(AuditLogRecord.category == category)
        if status:
            query = query.filter(AuditLogRecord.level == status)
        if failure_reason:
            query = query.filter(
                (AuditLogRecord.error_code == failure_reason) | (AuditLogRecord.event == failure_reason)
            )
        query = _apply_time_range(query, AuditLogRecord, created_after, created_before)
        query = _sort(
            query,
            AuditLogRecord,
            sort,
            {
                "created_at": AuditLogRecord.created_at,
                "status": AuditLogRecord.level,
                "category": AuditLogRecord.category,
            },
            "created_at",
        )
        rows, meta = _page(query, limit, offset)
        return Page(
            items=[
                AuditLogView(
                    id=row.id,
                    event=row.event,
                    level=row.level,
                    category=row.category,
                    correlation_id=row.correlation_id,
                    task_id=row.task_id,
                    subtask_id=row.subtask_id,
                    job_id=row.job_id,
                    node_id=row.node_id,
                    tool_run_id=row.tool_run_id,
                    artifact_id=row.artifact_id,
                    duration_ms=row.duration_ms or 0,
                    error_code=row.error_code,
                    payload=_safe_json(row.payload, {}),
                    created_at=row.created_at,
                )
                for row in rows
            ],
            page=meta,
        )


@app.post("/admin/retention/run", dependencies=[Depends(require_admin_auth)])
def run_database_retention(dry_run: bool = Query(default=False)) -> dict[str, int | bool]:
    result = run_retention_policy(dry_run=dry_run)
    return {**result, "dry_run": dry_run}


@app.get("/status", dependencies=[Depends(require_observability_auth)])
def cluster_status() -> dict[str, object]:
    mark_stale_nodes()
    with SessionLocal() as session:
        nodes = session.query(NodeRecord).order_by(NodeRecord.id).all()
        live_nodes = [node_to_dict(node) for node in nodes if node.status == "healthy"]
        gpu_vram_availability = []
        for node in nodes:
            reserved = json.loads(node.reserved_vram_json or "{}")
            for gpu in json.loads(node.gpu_json or "[]"):
                gpu_id = str(gpu.get("id") or "")
                free = float(gpu.get("vram_gb_free") or 0)
                gpu_vram_availability.append(
                    {
                        "node_id": node.id,
                        "gpu_id": gpu_id,
                        "vram_gb_free": free,
                        "reserved_vram_gb": float(reserved.get(gpu_id, 0)),
                        "available_after_reservation_gb": max(0.0, free - float(reserved.get(gpu_id, 0))),
                    }
                )
        recent_errors = [
            {
                "event": row.event,
                "task_id": row.task_id,
                "payload": _safe_json(row.payload, {}),
                "created_at": row.created_at,
            }
            for row in session.query(AuditLogRecord)
            .filter(
                AuditLogRecord.event.in_(
                    ["task_failed", "verification_failed", "final_check_failed", "evaluation_failed"]
                )
            )
            .order_by(AuditLogRecord.id.desc())
            .limit(20)
            .all()
        ]
        return {
            "live_nodes": live_nodes,
            "queued_tasks": session.query(TaskRecord).filter(TaskRecord.status.in_(["queued", "requeued"])).count(),
            "running_jobs": session.query(JobRecord).filter(JobRecord.status == "running").count(),
            "failed_jobs": session.query(JobRecord).filter(JobRecord.status.in_(["failed", "lost"])).count(),
            "gpu_vram_availability": gpu_vram_availability,
            "tool_runs": _counts_by(session, ToolRunRecord, "status"),
            "recent_errors": recent_errors,
        }


@app.get("/dashboard", response_class=HTMLResponse, dependencies=[Depends(rate_limit_observability)])
def dashboard() -> str:
    data = metrics()
    with SessionLocal() as session:
        recent = session.query(TaskRecord).order_by(TaskRecord.updated_at.desc()).limit(20).all()
    rows = "".join(
        f"<tr><td>{task.id}</td><td>{task.status}</td><td>{task.current_phase or ''}</td><td>{task.priority}</td><td>{task.workspace_id}</td></tr>"
        for task in recent
    )
    return f"""<!doctype html>
<html><head><title>Distributed Agent Dashboard</title>
<style>body{{font-family:system-ui,sans-serif;margin:2rem}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ccc;padding:.4rem;text-align:left}}code{{background:#eee;padding:.1rem .25rem}}</style>
</head><body>
<h1>Distributed Agent Dashboard</h1>
<p>Queue depth: <code>{data.queue_depth}</code></p>
<p>Tasks: <code>{data.tasks}</code></p>
<p>Nodes: <code>{data.nodes}</code> Availability: <code>{data.node_availability}</code></p>
<p>Jobs: running=<code>{data.running_jobs}</code> completed=<code>{data.completed_jobs}</code> failed=<code>{data.failed_jobs}</code> avg_duration_ms=<code>{round(data.average_job_duration_ms, 2)}</code></p>
<p>Tool runs: <code>{data.tool_runs}</code> timeouts=<code>{data.timeout_count}</code> retries=<code>{data.retry_count}</code></p>
<p>Optimization rounds: <code>{data.optimization_rounds}</code> Artifacts: <code>{data.artifacts}</code> Memories: <code>{data.memories}</code></p>
<h2>Recent tasks</h2>
<table><thead><tr><th>ID</th><th>Status</th><th>Phase</th><th>Priority</th><th>Workspace</th></tr></thead><tbody>{rows}</tbody></table>
</body></html>"""
