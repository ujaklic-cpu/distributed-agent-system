"""Canonical lifecycle status names used across the control plane.

The project intentionally keeps failure state names narrow:

* tasks and jobs use ``failed`` for terminal failures;
* canceled tasks use the American spelling ``canceled``;
* leases and stale jobs use structured ``error_code`` values instead of adding
  status variants such as ``expired`` or ``rejected``.
"""

from __future__ import annotations

from enum import Enum


class TaskStatusName(str, Enum):
    queued = "queued"
    planning = "planning"
    delegating = "delegating"
    running_agent = "running_agent"
    running_tool = "running_tool"
    evaluating = "evaluating"
    optimizing = "optimizing"
    final_check = "final_check"
    refining = "refining"
    completed = "completed"
    done = "done"
    verified = "verified"
    unverified = "unverified"
    failed = "failed"
    requeued = "requeued"
    canceled = "canceled"


class JobStatusName(str, Enum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    lost = "lost"


class ToolRunStatusName(str, Enum):
    ok = "ok"
    error = "error"
    timeout = "timeout"
    blocked = "blocked"
    canceled = "canceled"


class SubtaskStatusName(str, Enum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"


class OptimizationStatusName(str, Enum):
    running = "running"
    done = "done"
    failed = "failed"
    skipped = "skipped"


def values(enum_type: type[Enum]) -> tuple[str, ...]:
    """Return string values for an enum in declaration order."""
    return tuple(str(item.value) for item in enum_type)


TASK_STATUSES = values(TaskStatusName)
JOB_STATUSES = values(JobStatusName)
TOOL_RUN_STATUSES = values(ToolRunStatusName)
SUBTASK_STATUSES = values(SubtaskStatusName)
OPTIMIZATION_STATUSES = values(OptimizationStatusName)
TERMINAL_TASK_STATUSES = (
    TaskStatusName.completed.value,
    TaskStatusName.done.value,
    TaskStatusName.verified.value,
    TaskStatusName.unverified.value,
    TaskStatusName.failed.value,
    TaskStatusName.canceled.value,
)
TERMINAL_JOB_STATUSES = (JobStatusName.done.value, JobStatusName.failed.value, JobStatusName.lost.value)
