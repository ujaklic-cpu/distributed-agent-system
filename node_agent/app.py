from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from node_agent.config import settings
from node_agent.docker_manager import DockerToolRunner
from node_agent.heartbeat import (
    heartbeat_payload,
    register_payload,
    send_heartbeat,
    start_heartbeat_loop,
    stop_heartbeat_loop,
)
from node_agent.job_runner import AgentJobRunner
from node_agent.lifecycle import NodeRuntimeState, node_readiness, notify_orchestrator_drain
from node_agent.resource_probe import resource_snapshot
from shared.auth import parse_token_list, validate_role_token_list
from shared.logging import configure_logging, log_event
from shared.schemas import (
    AgentJobRequest,
    AgentJobResult,
    NodeHeartbeat,
    NodeRegister,
    ToolJobRequest,
    ToolJobResult,
    ToolMetadataView,
)
from shared.security import install_security_middleware

configure_logging(settings.log_level)
logger = logging.getLogger(__name__)

tool_runner = DockerToolRunner()
agent_runner = AgentJobRunner()
node_state = NodeRuntimeState()
_running_jobs = 0


def _running_job_count() -> int:
    return _running_jobs


def _heartbeat_status() -> str:
    snapshot = node_state.snapshot()
    if snapshot["draining"] or snapshot["shutting_down"]:
        return "draining"
    return "healthy"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    start_heartbeat_loop(_running_job_count, _heartbeat_status)
    try:
        yield
    finally:
        node_state.begin_shutdown()
        notify_orchestrator_drain(True)
        try:
            send_heartbeat(running_jobs=_running_job_count(), status="draining")
        finally:
            stop_heartbeat_loop()
        log_event(logger, "node_agent_shutdown", node_id=settings.node_id, running_jobs=_running_job_count())


app = FastAPI(title="Node Agent", version=settings.node_agent_version, lifespan=lifespan)
install_security_middleware(
    app,
    environment=settings.environment,
    cors_allow_origins=settings.cors_allow_origins,
    max_request_body_bytes=settings.max_request_body_bytes,
    max_response_body_bytes=settings.max_response_body_bytes,
)


def require_cluster_auth(request: Request, authorization: str | None = Header(default=None)) -> None:
    tokens = parse_token_list(settings.node_tokens) or (
        [] if settings.environment == "production" else parse_token_list(settings.cluster_token)
    )
    validate_role_token_list(
        authorization,
        tokens,
        realm="node",
        deprecated_tokens=parse_token_list(settings.deprecated_tokens),
        allow_empty=settings.environment != "production",
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "node_id": settings.node_id, "node_name": settings.node_name}


@app.get("/readyz")
def readyz() -> JSONResponse:
    ready, payload = node_readiness(node_state, _running_job_count())
    return JSONResponse(status_code=200 if ready else 503, content=payload)


@app.post("/drain", dependencies=[Depends(require_cluster_auth)])
def drain() -> dict:
    node_state.begin_drain()
    notify_orchestrator_drain(True)
    return {"status": "draining", "node_id": settings.node_id}


@app.post("/resume", dependencies=[Depends(require_cluster_auth)])
def resume() -> dict:
    node_state.resume()
    notify_orchestrator_drain(False)
    return {"status": "healthy", "node_id": settings.node_id}


@app.get("/resources")
def resources() -> dict:
    return resource_snapshot()


@app.get("/tools", response_model=list[ToolMetadataView])
def tool_metadata_endpoint() -> list[ToolMetadataView]:
    return [ToolMetadataView(**metadata.as_dict()) for metadata in tool_runner.metadata().values()]


@app.post("/jobs/agent", response_model=AgentJobResult, dependencies=[Depends(require_cluster_auth)])
def run_agent_job(job: AgentJobRequest) -> AgentJobResult:
    global _running_jobs
    if not node_state.snapshot()["accepting_jobs"]:
        raise HTTPException(status_code=503, detail="node is draining and not accepting new jobs")
    _running_jobs += 1
    log_event(logger, "agent_job_started", job_id=job.job_id, task_id=job.task_id, role=job.role.value)
    try:
        result = agent_runner.run(job)
        return result
    finally:
        _running_jobs = max(0, _running_jobs - 1)
        log_event(logger, "agent_job_finished", job_id=job.job_id, task_id=job.task_id)


@app.post("/jobs/{job_id}/cancel", dependencies=[Depends(require_cluster_auth)])
def cancel_job(job_id: str) -> dict[str, object]:
    killed = tool_runner.cancel(job_id)
    log_event(logger, "tool_job_cancel_requested", job_id=job_id, node_id=settings.node_id, killed_container=killed)
    return {"job_id": job_id, "status": "canceled", "killed_container": killed}


@app.post("/jobs/tool", response_model=ToolJobResult, dependencies=[Depends(require_cluster_auth)])
def run_tool_job(job: ToolJobRequest) -> ToolJobResult:
    global _running_jobs
    if not node_state.snapshot()["accepting_jobs"]:
        raise HTTPException(status_code=503, detail="node is draining and not accepting new jobs")
    _running_jobs += 1
    log_event(logger, "tool_job_started", job_id=job.job_id, task_id=job.task_id, tool=job.tool.value)
    try:
        result = tool_runner.run(job)
        return result
    finally:
        _running_jobs = max(0, _running_jobs - 1)
        log_event(logger, "tool_job_finished", job_id=job.job_id, task_id=job.task_id)


@app.get("/register-payload", response_model=NodeRegister)
def register_payload_endpoint() -> NodeRegister:
    return register_payload()


@app.get("/heartbeat-payload", response_model=NodeHeartbeat)
def heartbeat_payload_endpoint() -> NodeHeartbeat:
    return heartbeat_payload(running_jobs=_running_job_count(), status=_heartbeat_status())
