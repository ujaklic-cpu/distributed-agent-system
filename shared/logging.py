from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from shared.security import redact_value

LOG_LEVELS = {"debug", "info", "warning", "error", "critical"}
EVENT_CATEGORIES = {"scheduler", "node", "tool", "model", "verification", "artifact", "memory", "auth"}


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        stream=sys.stdout,
    )


def normalize_level(level: str | None) -> str:
    value = (level or "info").lower()
    return value if value in LOG_LEVELS else "info"


def infer_category(event: str, category: str | None = None) -> str:
    if category in EVENT_CATEGORIES:
        return category
    if event.startswith(("node_", "job_requeued")):
        return "node"
    if event.startswith(("tool_",)):
        return "tool"
    if event.startswith(("verification_", "final_check_", "evaluation_")):
        return "verification"
    if event.startswith(("artifact", "artifacts_")):
        return "artifact"
    if event.startswith(("memory",)):
        return "memory"
    if event.startswith(("auth",)):
        return "auth"
    if event.startswith(("plan_", "subtask_", "task_", "queue_")):
        return "scheduler"
    if event.startswith(("optimization_",)):
        return "model"
    return "scheduler"


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: str = "info",
    category: str | None = None,
    correlation_id: str | None = None,
    task_id: str | None = None,
    subtask_id: str | None = None,
    job_id: str | None = None,
    node_id: str | None = None,
    tool_run_id: str | None = None,
    artifact_id: str | None = None,
    duration_ms: int | None = None,
    error_code: str | None = None,
    **fields: Any,
) -> None:
    normalized_level = normalize_level(level)
    payload = {
        "ts": datetime.now(UTC).isoformat(),
        "level": normalized_level,
        "category": infer_category(event, category),
        "event": event,
        "correlation_id": correlation_id or task_id,
        "task_id": task_id,
        "subtask_id": subtask_id,
        "job_id": job_id,
        "node_id": node_id,
        "tool_run_id": tool_run_id,
        "artifact_id": artifact_id,
        "duration_ms": duration_ms,
        "error_code": error_code,
        **fields,
    }
    getattr(logger, normalized_level)(json.dumps(redact_value(payload), sort_keys=True, default=str))
