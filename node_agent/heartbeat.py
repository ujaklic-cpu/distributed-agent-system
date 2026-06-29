from __future__ import annotations

import logging
import threading

import httpx

from node_agent.config import settings
from node_agent.resource_probe import resource_snapshot
from shared.auth import cluster_auth_headers, parse_token_list
from shared.logging import log_event
from shared.schemas import NodeHeartbeat, NodeRegister

logger = logging.getLogger(__name__)

_started = False
_stop_event = threading.Event()


def register_payload() -> NodeRegister:
    snapshot = resource_snapshot()
    return NodeRegister(
        node_id=settings.node_id,
        hostname=settings.node_name,
        cpu_cores_total=snapshot["cpu_cores_total"],
        ram_gb_total=snapshot["ram_gb_total"],
        gpus=snapshot["gpus"],
        capabilities=settings.capabilities_list(),
        supported_tools=settings.supported_tools_list(),
        docker_available=bool(snapshot.get("docker_available", False)),
        platform_arch=str(snapshot.get("platform_arch") or "unknown"),
        agent_version=settings.node_agent_version,
        protocol_version=settings.protocol_version,
        base_url=settings.public_node_url,
    )


def heartbeat_payload(
    running_jobs: int = 0, loaded_models: list[str] | None = None, status: str = "healthy"
) -> NodeHeartbeat:
    snapshot = resource_snapshot()
    return NodeHeartbeat(
        status=status,
        cpu_cores_free=snapshot["cpu_cores_free"],
        ram_gb_free=snapshot["ram_gb_free"],
        gpus=snapshot["gpus"],
        running_jobs=running_jobs,
        loaded_models=loaded_models or settings.loaded_models_list(),
        capabilities=settings.capabilities_list(),
        supported_tools=settings.supported_tools_list(),
        docker_available=bool(snapshot.get("docker_available", False)),
        platform_arch=str(snapshot.get("platform_arch") or "unknown"),
        agent_version=settings.node_agent_version,
        protocol_version=settings.protocol_version,
    )


def register_once() -> None:
    if not settings.orchestrator_url:
        return
    url = settings.orchestrator_url.rstrip("/")
    with httpx.Client(timeout=10) as client:
        response = client.post(
            f"{url}/nodes/register",
            json=register_payload().model_dump(mode="json"),
            headers=cluster_auth_headers((parse_token_list(settings.node_tokens) or [settings.cluster_token])[0]),
        )
        response.raise_for_status()
    log_event(logger, "node_registered_with_orchestrator", node_id=settings.node_id, orchestrator_url=url)


def send_heartbeat(running_jobs: int = 0, status: str = "healthy") -> None:
    if not settings.orchestrator_url:
        return
    url = settings.orchestrator_url.rstrip("/")
    with httpx.Client(timeout=10) as client:
        response = client.post(
            f"{url}/nodes/{settings.node_id}/heartbeat",
            json=heartbeat_payload(running_jobs=running_jobs, status=status).model_dump(mode="json"),
            headers=cluster_auth_headers((parse_token_list(settings.node_tokens) or [settings.cluster_token])[0]),
        )
        response.raise_for_status()


def start_heartbeat_loop(get_running_jobs, get_status=lambda: "healthy") -> None:
    global _started
    if _started or not settings.orchestrator_url:
        return
    _started = True

    def loop() -> None:
        while not _stop_event.is_set():
            try:
                register_once()
                break
            except Exception as exc:
                log_event(logger, "node_register_failed", error=str(exc))
                _stop_event.wait(settings.heartbeat_interval_seconds)
        while not _stop_event.is_set():
            try:
                send_heartbeat(running_jobs=int(get_running_jobs()), status=str(get_status()))
            except Exception as exc:
                log_event(logger, "node_heartbeat_failed", error=str(exc))
            _stop_event.wait(settings.heartbeat_interval_seconds)

    threading.Thread(target=loop, daemon=True).start()


def stop_heartbeat_loop() -> None:
    global _started
    _stop_event.set()
    _started = False
