from __future__ import annotations

import logging
import signal
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from redis import Redis
from sqlalchemy import text

from orchestrator.config import settings
from orchestrator.db import JobRecord, NodeRecord, SessionLocal, engine
from orchestrator.task_queue import RedisTaskQueue
from shared.logging import log_event

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    ready: bool
    detail: str = "ok"


class RuntimeState:
    """Tracks process readiness and shutdown state shared by API handlers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.accepting_jobs = True
        self.shutting_down = False
        self.started_at = time.time()

    def begin_shutdown(self) -> None:
        """Stop admission immediately and mark the process as terminating."""
        with self._lock:
            self.accepting_jobs = False
            self.shutting_down = True

    def pause_admission(self) -> None:
        """Stop accepting new jobs without marking the process fully stopped."""
        with self._lock:
            self.accepting_jobs = False

    def resume_admission(self) -> None:
        """Resume accepting new jobs after a drain window."""
        with self._lock:
            if not self.shutting_down:
                self.accepting_jobs = True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "accepting_jobs": self.accepting_jobs,
                "shutting_down": self.shutting_down,
                "uptime_seconds": max(0, int(time.time() - self.started_at)),
            }


def _check_database() -> ReadinessCheck:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return ReadinessCheck("database", True)
    except Exception as exc:
        return ReadinessCheck("database", False, str(exc))


def _check_redis() -> ReadinessCheck:
    try:
        Redis.from_url(settings.queue_url, decode_responses=True).ping()
        return ReadinessCheck("redis", True)
    except Exception as exc:
        return ReadinessCheck("redis", False, str(exc))


def _check_writable_path(name: str, value: str) -> ReadinessCheck:
    try:
        path = Path(value)
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".readyz"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return ReadinessCheck(name, True)
    except Exception as exc:
        return ReadinessCheck(name, False, str(exc))


def orchestrator_readiness(state: RuntimeState) -> tuple[bool, dict[str, Any]]:
    """Return readiness details for load balancers and rolling updates."""
    snapshot = state.snapshot()
    checks = [
        ReadinessCheck(
            "admission",
            bool(snapshot["accepting_jobs"]),
            "accepting jobs" if snapshot["accepting_jobs"] else "draining",
        ),
        _check_database(),
        _check_redis(),
        _check_writable_path("workspace_root", settings.orchestrator_workspace_root),
        _check_writable_path("artifact_root", settings.artifact_storage_root),
    ]
    ready = all(check.ready for check in checks) and not snapshot["shutting_down"]
    return ready, {
        "status": "ready" if ready else "not_ready",
        **snapshot,
        "checks": [check.__dict__ for check in checks],
    }


def close_orchestrator_resources(queue: RedisTaskQueue) -> None:
    """Close long-lived clients during graceful process shutdown."""
    try:
        queue.redis.close()
    except Exception as exc:
        log_event(logger, "queue_close_failed", level="warning", error=str(exc))
    try:
        engine.dispose()
    except Exception as exc:
        log_event(logger, "database_engine_dispose_failed", level="warning", error=str(exc))


def mark_node_draining_for_shutdown(node_id: str) -> None:
    """Mark a node as draining so the scheduler stops assigning it fresh work."""
    with SessionLocal() as session:
        node = session.get(NodeRecord, node_id)
        if node and node.status not in {"dead", "incompatible"}:
            node.status = "draining"
            node.version += 1
            session.commit()


def requeue_active_jobs_for_node_shutdown(node_id: str) -> list[str]:
    """Requeue jobs owned by a terminating node-agent.

    Running jobs are marked lost and released. The orchestrator worker will place
    their parent tasks back on the queue on the next recovery pass.
    """
    job_ids: list[str] = []
    with SessionLocal() as session:
        rows = (
            session.query(JobRecord).filter(JobRecord.assigned_node_id == node_id, JobRecord.status == "running").all()
        )
        for job in rows:
            job.status = "lost"
            job.lost_at = job.lost_at or __import__("datetime").datetime.now(__import__("datetime").UTC)
            job.error_code = "NODE_SHUTDOWN_REQUEUED"
            job.reserved_cpu_cores = 0
            job.reserved_ram_gb = 0
            job.reserved_vram_json = "{}"
            job_ids.append(job.id)
        node = session.get(NodeRecord, node_id)
        if node:
            node.running_jobs = 0
            node.reserved_cpu_cores = 0
            node.reserved_ram_gb = 0
            node.reserved_vram_json = "{}"
            node.version += 1
        session.commit()
    return job_ids


class WorkerShutdown:
    """Signal-aware stop flag for the queue worker."""

    def __init__(self) -> None:
        self.stop_event = threading.Event()

    def install(self) -> None:
        def handler(signum: int, frame: object | None) -> None:
            self.stop_event.set()
            log_event(logger, "queue_worker_shutdown_requested", signal=signum)

        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGINT, handler)

    def should_stop(self) -> bool:
        return self.stop_event.is_set()
