from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from node_agent.config import settings
from node_agent.resource_probe import resource_snapshot
from shared.auth import cluster_auth_headers
from shared.logging import log_event

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    ready: bool
    detail: str = "ok"


class NodeRuntimeState:
    """Tracks admission and drain state for a node-agent process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.accepting_jobs = True
        self.draining = False
        self.shutting_down = False
        self.started_at = time.time()

    def begin_drain(self) -> None:
        with self._lock:
            self.accepting_jobs = False
            self.draining = True

    def resume(self) -> None:
        with self._lock:
            if not self.shutting_down:
                self.accepting_jobs = True
                self.draining = False

    def begin_shutdown(self) -> None:
        with self._lock:
            self.accepting_jobs = False
            self.draining = True
            self.shutting_down = True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "accepting_jobs": self.accepting_jobs,
                "draining": self.draining,
                "shutting_down": self.shutting_down,
                "uptime_seconds": max(0, int(time.time() - self.started_at)),
            }


def node_readiness(state: NodeRuntimeState, running_jobs: int) -> tuple[bool, dict[str, Any]]:
    """Return node readiness; not-ready nodes should be removed from rotation."""
    snapshot = state.snapshot()
    checks: list[ReadinessCheck] = [
        ReadinessCheck(
            "admission",
            bool(snapshot["accepting_jobs"]),
            "accepting jobs" if snapshot["accepting_jobs"] else "draining",
        ),
    ]
    try:
        resources = resource_snapshot()
        checks.append(ReadinessCheck("resources", True))
        if any(item.startswith("tool_") for item in settings.capabilities_list()):
            checks.append(
                ReadinessCheck(
                    "docker",
                    bool(resources.get("docker_available")),
                    "docker available" if resources.get("docker_available") else "docker unavailable",
                )
            )
    except Exception as exc:
        checks.append(ReadinessCheck("resources", False, str(exc)))
    if settings.orchestrator_url:
        try:
            response = httpx.get(
                f"{settings.orchestrator_url.rstrip('/')}/health", timeout=settings.readiness_check_timeout_seconds
            )
            checks.append(ReadinessCheck("orchestrator", response.status_code < 500, f"http {response.status_code}"))
        except Exception as exc:
            checks.append(ReadinessCheck("orchestrator", False, str(exc)))
    ready = all(check.ready for check in checks) and not snapshot["shutting_down"]
    return ready, {
        "status": "ready" if ready else "not_ready",
        "node_id": settings.node_id,
        "running_jobs": running_jobs,
        **snapshot,
        "checks": [check.__dict__ for check in checks],
    }


def notify_orchestrator_drain(enabled: bool) -> None:
    """Best-effort request for the orchestrator to stop or resume scheduling this node."""
    if not settings.orchestrator_url:
        return
    suffix = "drain" if enabled else "resume"
    try:
        httpx.post(
            f"{settings.orchestrator_url.rstrip('/')}/nodes/{settings.node_id}/{suffix}",
            headers=cluster_auth_headers(settings.cluster_token),
            timeout=settings.readiness_check_timeout_seconds,
        )
    except Exception as exc:
        log_event(logger, "node_drain_notification_failed", level="warning", enabled=enabled, error=str(exc))
