from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from orchestrator.artifacts import collect_workspace_artifacts
from orchestrator.config import settings
from orchestrator.db import (
    ArtifactRecord,
    AuditLogRecord,
    OptimizationRoundRecord,
    SessionLocal,
    SubtaskRecord,
    TaskRecord,
    ToolRunRecord,
)
from orchestrator.model_client import NodeAgentModelClient
from orchestrator.registry import (
    acquire_workspace_lock,
    attach_execution_context,
    create_reserved_job,
    finish_job_from_result,
    release_workspace_lock,
)
from orchestrator.routing import clamp_score, requires_smart_review, route_subtask
from orchestrator.scheduler import role_requirements, tool_requirements
from orchestrator.tool_client import NodeAgentToolClient
from orchestrator.verification import criteria_to_specs, unavailable, verify_all
from shared.hashing import plan_hash, tool_call_hash
from shared.logging import infer_category, log_event, normalize_level
from shared.messages import (
    EVALUATOR_PROMPT,
    FINAL_CHECK_PROMPT,
    OPTIMIZER_PROMPT,
    SMART_PLANNING_PROMPT,
    SMART_REVIEW_PROMPT,
    WORKER_PROMPT,
)
from shared.policy import DEFAULT_TOOL_POLICY
from shared.schemas import (
    AgentChatResponse,
    AgentJobRequest,
    AgentMessage,
    AgentRole,
    TaskMode,
    TaskStatus,
    ToolJobRequest,
    ToolJobResult,
    ToolLimits,
    ToolRequest,
    new_id,
)

logger = logging.getLogger(__name__)


@dataclass
class SubtaskPlan:
    id: str
    description: str
    difficulty: int
    risk: int
    executor: AgentRole
    success_condition: str
    requested_executor: AgentRole | None = None
    routing_reason: str = ""
    smart_review_required: bool = False


def _elapsed_ms(start: datetime | None, end: datetime | None = None) -> int:
    if start is None:
        return 0
    end = end or _utcnow()
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return max(0, int((end - start).total_seconds() * 1000))


def _audit(session, event: str, task_id: str | None, payload: dict[str, Any]) -> None:
    task = session.get(TaskRecord, task_id) if task_id else None
    enriched = dict(payload)
    level = normalize_level(str(enriched.pop("level", "info")))
    category = infer_category(event, enriched.pop("category", None))
    correlation_id = (
        str(enriched.pop("correlation_id", None) or (task.correlation_id if task else None) or task_id or "") or None
    )
    subtask_id = enriched.pop("subtask_id", None)
    job_id = enriched.pop("job_id", None)
    node_id = enriched.pop("node_id", None)
    tool_run_id = enriched.pop("tool_run_id", None)
    artifact_id = enriched.pop("artifact_id", None)
    error_code = enriched.pop("error_code", None)
    duration_ms = int(enriched.pop("duration_ms", 0) or 0)
    session.add(
        AuditLogRecord(
            event=event,
            level=level,
            category=category,
            correlation_id=correlation_id,
            task_id=task_id,
            subtask_id=str(subtask_id) if subtask_id is not None else None,
            job_id=str(job_id) if job_id is not None else None,
            node_id=str(node_id) if node_id is not None else None,
            tool_run_id=str(tool_run_id) if tool_run_id is not None else None,
            artifact_id=str(artifact_id) if artifact_id is not None else None,
            duration_ms=duration_ms,
            error_code=str(error_code) if error_code is not None else None,
            payload=json.dumps(enriched, default=str),
        )
    )
    log_event(
        logger,
        event,
        level=level,
        category=category,
        correlation_id=correlation_id,
        task_id=task_id,
        subtask_id=str(subtask_id) if subtask_id is not None else None,
        job_id=str(job_id) if job_id is not None else None,
        node_id=str(node_id) if node_id is not None else None,
        tool_run_id=str(tool_run_id) if tool_run_id is not None else None,
        artifact_id=str(artifact_id) if artifact_id is not None else None,
        duration_ms=duration_ms,
        error_code=str(error_code) if error_code is not None else None,
        **enriched,
    )


def _job_terminal_status(result_status: str) -> str:
    """Map execution result statuses onto the canonical job lifecycle."""
    return "done" if result_status == "ok" else "failed"


def _tool_error_code(status: str) -> str | None:
    """Translate tool statuses into stable operator-facing error codes."""
    return {
        "timeout": "TOOL_TIMEOUT",
        "blocked": "TOOL_BLOCKED",
        "error": "TOOL_ERROR",
        "canceled": "TOOL_CANCELED",
    }.get(status)


def _finish_task_as_failed(task_id: str, error: str, error_code: str) -> None:
    """Persist a terminal task failure with duration and audit metadata."""
    with SessionLocal() as session:
        task = session.get(TaskRecord, task_id)
        if task is None:
            return
        task.status = TaskStatus.failed.value
        task.current_phase = "failed"
        task.error = error
        task.error_code = error_code
        task.completed_at = task.completed_at or _utcnow()
        task.duration_ms = _elapsed_ms(task.created_at, task.completed_at)
        _audit(
            session,
            "task_failed",
            task_id,
            {
                "level": "error",
                "error": task.error,
                "error_code": task.error_code,
                "duration_ms": task.duration_ms,
            },
        )
        session.commit()


def _finish_job_or_raise(
    job_id: str, result: Any, status: str, task_id: str | None = None, phase: str | None = None
) -> None:
    """Finish a reserved job only if the registry accepts this exact result.

    The registry rejects stale attempts, wrong-node responses, expired leases, and
    generation mismatches. Callers must not continue with an agent/tool payload
    that failed this acceptance check.
    """
    accepted, reason = finish_job_from_result(job_id, result, status)
    if accepted:
        return

    payload: dict[str, Any]
    try:
        payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result or {})
    except Exception:
        payload = {"result_type": type(result).__name__}

    event_payload = {
        "job_id": job_id,
        "status": status,
        "reason": reason,
        "phase": phase,
        "attempt_id": payload.get("attempt_id"),
        "generation": payload.get("generation"),
        "node_id": payload.get("node_id"),
    }
    if task_id:
        with SessionLocal() as session:
            _audit(session, "job_result_rejected", task_id, event_payload)
            session.commit()
    else:
        log_event(logger, "job_result_rejected", task_id=task_id, **event_payload)
    raise RuntimeError(f"job result rejected: {reason}")


def _parse_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    candidates = [text]
    if "```" in text:
        chunks = text.split("```")
        candidates.extend(chunk.strip().removeprefix("json").strip() for chunk in chunks)
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(text[start : end + 1])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            return None
    return None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _task_success_criteria(task: TaskRecord | None) -> dict[str, Any]:
    if not task:
        return {}
    try:
        value = json.loads(task.success_criteria_json or "{}")
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _has_explicit_success_criteria(criteria: dict[str, Any]) -> bool:
    return bool(
        criteria.get("required_files")
        or criteria.get("required_commands")
        or criteria.get("expected_output_type")
        or criteria.get("verification_command")
        or criteria.get("acceptance_criteria")
    )


def _fallback_plan(mode: TaskMode = TaskMode.normal) -> list[SubtaskPlan]:
    decision = route_subtask(
        requested_executor=AgentRole.smart_agent,
        difficulty=7,
        risk=5,
        description="Complete the requested goal and produce a final result in the workspace.",
        mode=mode,
    )
    return [
        SubtaskPlan(
            id="T1",
            description="Complete the requested goal and produce a final result in the workspace.",
            difficulty=7,
            risk=5,
            executor=decision.executor,
            requested_executor=AgentRole.smart_agent,
            routing_reason=decision.reason,
            smart_review_required=decision.review_required,
            success_condition="A final response or artifact is produced and no tool execution failed.",
        )
    ]


class TaskRunner:
    def __init__(self) -> None:
        self.model_client = NodeAgentModelClient()
        self.tool_client = NodeAgentToolClient()

    def run_task(self, task_id: str) -> None:
        with SessionLocal() as session:
            task = session.get(TaskRecord, task_id)
            if task is None:
                return
            if task.status in {TaskStatus.done.value, TaskStatus.failed.value, TaskStatus.canceled.value}:
                return
            task.status = TaskStatus.planning.value
            task.current_phase = "planning"
            session.commit()

        try:
            self._run_task_attempts(task_id)
        except Exception as exc:
            _finish_task_as_failed(task_id, str(exc), "TASK_UNHANDLED_EXCEPTION")

    def _run_task_attempts(self, task_id: str) -> None:
        seen_plan_hashes: set[str] = set()
        for _ in range(settings.max_task_attempts):
            with SessionLocal() as session:
                task = session.get(TaskRecord, task_id)
                assert task is not None
                task.attempts += 1
                task.status = TaskStatus.planning.value
                task.current_phase = "planning"
                _audit(session, "task_attempt_started", task_id, {"attempt": task.attempts})
                session.commit()
                goal = task.goal
                workspace_id = task.workspace_id
                mode = TaskMode(task.mode)
                attempt = task.attempts

            plan = self._plan(task_id, goal, workspace_id, mode)
            plan_payload = {
                "subtasks": [
                    p.__dict__
                    | {
                        "executor": p.executor.value,
                        "requested_executor": p.requested_executor.value if p.requested_executor else None,
                    }
                    for p in plan
                ]
            }
            plan_digest = plan_hash(plan_payload)
            if plan_digest in seen_plan_hashes:
                plan = _fallback_plan(mode)
                plan_digest = plan_hash(
                    {
                        "subtasks": [p.__dict__ | {"executor": p.executor.value} for p in plan],
                        "fallback": True,
                        "attempt": attempt,
                    }
                )
            seen_plan_hashes.add(plan_digest)

            with SessionLocal() as session:
                task = session.get(TaskRecord, task_id)
                assert task is not None
                task.status = TaskStatus.delegating.value
                task.current_phase = "delegating"
                _audit(
                    session,
                    "plan_generated",
                    task_id,
                    {"attempt": attempt, "plan_hash": plan_digest, "subtask_count": len(plan)},
                )
                for item in plan:
                    session.merge(
                        SubtaskRecord(
                            id=f"{task_id}_{item.id}",
                            correlation_id=task.correlation_id,
                            task_id=task_id,
                            description=item.description,
                            status="queued",
                            executor_type=item.executor.value,
                            difficulty=item.difficulty,
                            risk=item.risk,
                            success_condition=item.success_condition,
                        )
                    )
                    _audit(
                        session,
                        "subtask_routed",
                        task_id,
                        {
                            "subtask_id": item.id,
                            "requested_executor": item.requested_executor.value if item.requested_executor else None,
                            "executor": item.executor.value,
                            "difficulty": item.difficulty,
                            "risk": item.risk,
                            "review_required": item.smart_review_required,
                            "reason": item.routing_reason,
                        },
                    )
                session.commit()

            results: list[dict[str, Any]] = []
            for subtask in plan:
                result = self._run_subtask(task_id, goal, workspace_id, mode, subtask)
                results.append(result)

            evaluation = self._evaluate(task_id, goal, workspace_id, mode, results)
            if evaluation.get("passed") is True:
                base_result = self._format_final_result(goal, results, evaluation)
                optimized = self._optimize_result(task_id, goal, workspace_id, mode, results, evaluation, base_result)
                final_result = str(optimized.get("result") or base_result)
                artifacts = self._persist_artifacts(task_id, workspace_id)
                final_check = self._final_check(
                    task_id, goal, workspace_id, mode, final_result, artifacts, optimized, results
                )
                if final_check.get("passed") is True:
                    with SessionLocal() as session:
                        task = session.get(TaskRecord, task_id)
                        assert task is not None
                        task.status = TaskStatus.done.value
                        task.current_phase = "done:verified"
                        task.result = final_result
                        task.artifacts_json = json.dumps(artifacts)
                        task.completed_at = _utcnow()
                        task.verified_at = _utcnow()
                        task.duration_ms = _elapsed_ms(task.created_at, task.completed_at)
                        task.verification_json = json.dumps(final_check, default=str)
                        _audit(
                            session,
                            "task_done",
                            task_id,
                            {"verification": final_check, "artifacts": artifacts, "duration_ms": task.duration_ms},
                        )
                        session.commit()
                    return

                is_unverified = final_check.get("status") == "unverified" or final_check.get("passed") is None
                with SessionLocal() as session:
                    task = session.get(TaskRecord, task_id)
                    assert task is not None
                    task.result = final_result
                    task.artifacts_json = json.dumps(artifacts)
                    task.completed_at = task.completed_at or _utcnow()
                    task.verification_json = json.dumps(final_check, default=str)
                    if is_unverified:
                        task.status = TaskStatus.unverified.value
                        task.current_phase = "completed:unverified"
                        task.error = "Task execution completed, but verification was unavailable or insufficient."
                        task.error_code = "VERIFICATION_UNAVAILABLE"
                        task.duration_ms = _elapsed_ms(task.created_at, task.completed_at)
                        _audit(
                            session,
                            "verification_unavailable",
                            task_id,
                            {
                                **final_check,
                                "level": "warning",
                                "error_code": task.error_code,
                                "duration_ms": task.duration_ms,
                            },
                        )
                    else:
                        task.status = TaskStatus.refining.value
                        task.current_phase = "refining:verification_failed"
                        task.error = json.dumps(final_check)
                        task.error_code = "VERIFICATION_FAILED"
                        _audit(
                            session,
                            "verification_failed",
                            task_id,
                            {**final_check, "level": "warning", "error_code": task.error_code},
                        )
                    session.commit()
                if is_unverified:
                    return
                continue

            with SessionLocal() as session:
                task = session.get(TaskRecord, task_id)
                assert task is not None
                task.status = TaskStatus.refining.value
                task.current_phase = "refining"
                task.error = json.dumps(evaluation)
                task.error_code = "EVALUATION_FAILED"
                _audit(
                    session,
                    "evaluation_failed",
                    task_id,
                    {**evaluation, "level": "warning", "error_code": task.error_code},
                )
                session.commit()

        _finish_task_as_failed(task_id, "Maximum attempts reached", "TASK_MAX_ATTEMPTS_REACHED")

    def _plan(self, task_id: str, goal: str, workspace_id: str, mode: TaskMode) -> list[SubtaskPlan]:
        request = AgentJobRequest(
            task_id=task_id,
            role=AgentRole.smart_agent,
            workspace_id=workspace_id,
            messages=[
                AgentMessage(role="system", content=SMART_PLANNING_PROMPT),
                AgentMessage(role="user", content=f"Goal: {goal}\nWorkspace: {workspace_id}\nMode: {mode.value}"),
            ],
        )
        reqs = role_requirements(AgentRole.smart_agent, mode)
        job_id, node = create_reserved_job(task_id, reqs)
        if node is None:
            raise RuntimeError("no node available for planning")
        attach_execution_context(request, job_id)
        result = self.model_client.run_agent(request, base_url=node.base_url)
        _finish_job_or_raise(job_id, result, _job_terminal_status(result.status), task_id, "planning")
        if result.status != "ok" or not result.response:
            return _fallback_plan(mode)
        parsed = _parse_json_object(result.response.content)
        if not parsed or not isinstance(parsed.get("subtasks"), list):
            return _fallback_plan(mode)
        subtasks: list[SubtaskPlan] = []
        for i, raw in enumerate(parsed["subtasks"], start=1):
            if not isinstance(raw, dict):
                continue
            try:
                requested = AgentRole(str(raw.get("executor") or "smart_agent"))
            except Exception:
                requested = AgentRole.smart_agent
            difficulty = clamp_score(raw.get("difficulty"), 5)
            risk = clamp_score(raw.get("risk"), 5)
            description = str(raw.get("description") or "Complete subtask.")
            success_condition = str(raw.get("success_condition") or "Subtask completed.")
            decision = route_subtask(
                requested_executor=requested,
                difficulty=difficulty,
                risk=risk,
                description=description,
                success_condition=success_condition,
                mode=mode,
            )
            subtasks.append(
                SubtaskPlan(
                    id=str(raw.get("id") or f"T{i}"),
                    description=description,
                    difficulty=difficulty,
                    risk=risk,
                    executor=decision.executor,
                    requested_executor=requested,
                    routing_reason=decision.reason,
                    smart_review_required=decision.review_required,
                    success_condition=success_condition,
                )
            )
        return subtasks or _fallback_plan(mode)

    def _run_subtask(
        self, task_id: str, goal: str, workspace_id: str, mode: TaskMode, subtask: SubtaskPlan
    ) -> dict[str, Any]:
        result = self._run_agent_subtask(task_id, goal, workspace_id, mode, subtask, subtask.executor)
        if not result.get("success") and subtask.executor != AgentRole.smart_agent:
            escalated = self._run_agent_subtask(
                task_id, goal, workspace_id, mode, subtask, AgentRole.smart_agent, previous_result=result
            )
            escalated["escalated_from"] = subtask.executor.value
            return escalated
        if subtask.smart_review_required or requires_smart_review(
            subtask.executor, subtask.difficulty, subtask.risk, bool(result.get("success"))
        ):
            review = self._smart_review(task_id, goal, workspace_id, mode, subtask, result)
            result["smart_review"] = review
            if review.get("passed") is False:
                escalated = self._run_agent_subtask(
                    task_id, goal, workspace_id, mode, subtask, AgentRole.smart_agent, previous_result=result
                )
                escalated["escalated_from"] = subtask.executor.value
                escalated["review"] = review
                return escalated
        return result

    def _run_agent_subtask(
        self,
        task_id: str,
        goal: str,
        workspace_id: str,
        mode: TaskMode,
        subtask: SubtaskPlan,
        executor: AgentRole,
        previous_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with SessionLocal() as session:
            task = session.get(TaskRecord, task_id)
            assert task is not None
            task.status = TaskStatus.running_agent.value
            task.current_phase = f"running_agent:{subtask.id}:{executor.value}"
            subtask_record = session.get(SubtaskRecord, f"{task_id}_{subtask.id}")
            if subtask_record:
                subtask_record.status = "running"
                subtask_record.executor_type = executor.value
                subtask_record.started_at = subtask_record.started_at or _utcnow()
            _audit(session, "subtask_started", task_id, {"subtask_id": subtask.id, "executor": executor.value})
            session.commit()

        previous = (
            f"\nPrevious worker result to fix or complete:\n{json.dumps(previous_result, default=str)[:6000]}"
            if previous_result
            else ""
        )
        messages = [
            AgentMessage(role="system", content=WORKER_PROMPT),
            AgentMessage(
                role="user",
                content=(
                    f"Original goal: {goal}\n"
                    f"Workspace id: {workspace_id}\n"
                    f"Subtask: {subtask.description}\n"
                    f"Difficulty: {subtask.difficulty}\nRisk: {subtask.risk}\n"
                    f"Success condition: {subtask.success_condition}\n"
                    f"Executor role: {executor.value}\n"
                    "Return tool_requests as structured JSON when tools are needed."
                    f"{previous}"
                ),
            ),
        ]
        tool_history: list[dict[str, Any]] = []
        repeated_tool_hashes: set[str] = set()
        final_content = ""

        reqs = role_requirements(executor, mode)
        node_id = None
        node_url = None
        with SessionLocal() as session:
            task = session.get(TaskRecord, task_id)
            if task and node_id:
                assigned = json.loads(task.assigned_nodes or "[]")
                if node_id not in assigned:
                    assigned.append(node_id)
                task.assigned_nodes = json.dumps(assigned)
            subtask_record = session.get(SubtaskRecord, f"{task_id}_{subtask.id}")
            if subtask_record and node_id:
                subtask_record.assigned_node_id = node_id
            session.commit()

        for _step in range(settings.max_agent_steps):
            agent_job = AgentJobRequest(
                task_id=task_id,
                subtask_id=subtask.id,
                role=executor,
                workspace_id=workspace_id,
                messages=messages,
            )
            job_id, node = create_reserved_job(task_id, reqs, f"{task_id}_{subtask.id}")
            if node is None:
                raise RuntimeError(f"no node available for {executor.value}")
            attach_execution_context(agent_job, job_id)
            node_id = node.id
            node_url = node.base_url
            with SessionLocal() as session:
                task = session.get(TaskRecord, task_id)
                if task:
                    assigned = json.loads(task.assigned_nodes or "[]")
                    if node_id not in assigned:
                        assigned.append(node_id)
                    task.assigned_nodes = json.dumps(assigned)
                subtask_record = session.get(SubtaskRecord, f"{task_id}_{subtask.id}")
                if subtask_record:
                    subtask_record.assigned_node_id = node_id
                session.commit()
            agent_result = self.model_client.run_agent(agent_job, base_url=node_url)
            _finish_job_or_raise(job_id, agent_result, _job_terminal_status(agent_result.status), task_id, "agent")
            if agent_result.status != "ok" or not agent_result.response:
                final_content = agent_result.error or "agent error"
                break

            response = AgentChatResponse.model_validate(agent_result.response)
            final_content = response.content
            messages.append(AgentMessage(role="assistant", content=response.content))

            if not response.tool_requests:
                break

            if len(response.tool_requests) > settings.max_tool_calls_per_agent_step:
                final_content = "Agent requested too many tools in one step."
                break

            blocked = False
            for req in response.tool_requests:
                tool_result = self._run_tool_request(task_id, workspace_id, f"{task_id}_{subtask.id}", req, repeated_tool_hashes)
                tool_history.append(tool_result)
                messages.append(AgentMessage(role="tool", content=json.dumps(tool_result, default=str)))
                if tool_result.get("status") in {"blocked", "timeout"}:
                    final_content = json.dumps(tool_result)
                    blocked = True
                    break
            if blocked:
                break

        if tool_history:
            success = all(item.get("status") == "ok" and item.get("exit_code") == 0 for item in tool_history)
        else:
            parsed_success = _parse_json_object(final_content)
            success = bool(
                parsed_success and (parsed_success.get("success") is True or parsed_success.get("passed") is True)
            )
        with SessionLocal() as session:
            subtask_record = session.get(SubtaskRecord, f"{task_id}_{subtask.id}")
            if subtask_record:
                subtask_record.status = "done" if success else "failed"
            subtask_record = session.get(SubtaskRecord, f"{task_id}_{subtask.id}")
            duration_ms = 0
            if subtask_record:
                subtask_record.finished_at = _utcnow()
                subtask_record.duration_ms = _elapsed_ms(subtask_record.started_at, subtask_record.finished_at)
                if not success:
                    subtask_record.error_code = "SUBTASK_FAILED"
                duration_ms = subtask_record.duration_ms
            _audit(
                session,
                "subtask_finished",
                task_id,
                {
                    "subtask_id": subtask.id,
                    "executor": executor.value,
                    "success": success,
                    "duration_ms": duration_ms,
                    "error_code": "SUBTASK_FAILED" if not success else None,
                },
            )
            session.commit()
        return {
            "subtask_id": subtask.id,
            "executor": executor.value,
            "requested_executor": subtask.requested_executor.value if subtask.requested_executor else None,
            "routing_reason": subtask.routing_reason,
            "difficulty": subtask.difficulty,
            "risk": subtask.risk,
            "content": final_content,
            "tool_history": tool_history,
            "success": success,
        }

    def _smart_review(
        self, task_id: str, goal: str, workspace_id: str, mode: TaskMode, subtask: SubtaskPlan, result: dict[str, Any]
    ) -> dict[str, Any]:
        request = AgentJobRequest(
            task_id=task_id,
            subtask_id=subtask.id,
            role=AgentRole.smart_agent,
            workspace_id=workspace_id,
            messages=[
                AgentMessage(role="system", content=SMART_REVIEW_PROMPT),
                AgentMessage(
                    role="user",
                    content=json.dumps(
                        {
                            "goal": goal,
                            "subtask": subtask.__dict__ | {"executor": subtask.executor.value},
                            "result": result,
                        },
                        default=str,
                    ),
                ),
            ],
        )
        reqs = role_requirements(AgentRole.smart_agent, mode)
        job_id, node = create_reserved_job(task_id, reqs, f"{task_id}_{subtask.id}", job_type="smart_review")
        if node is None:
            raise RuntimeError("no node available for smart_review")
        attach_execution_context(request, job_id)
        agent_result = self.model_client.run_agent(request, base_url=node.base_url)
        _finish_job_or_raise(job_id, agent_result, _job_terminal_status(agent_result.status), task_id, "agent")
        if agent_result.status == "ok" and agent_result.response:
            parsed = _parse_json_object(agent_result.response.content)
            if parsed and "passed" in parsed:
                parsed["reviewer"] = AgentRole.smart_agent.value
                return parsed
        return {
            "passed": bool(result.get("success")),
            "score": 80 if result.get("success") else 30,
            "problems": [] if result.get("success") else ["Smart review fallback could not verify worker result."],
            "next_action": "accept" if result.get("success") else "fix_directly",
            "reviewer": AgentRole.smart_agent.value,
        }

    def _run_tool_request(
        self,
        task_id: str,
        workspace_id: str,
        subtask_id: str,
        request: ToolRequest,
        repeated_tool_hashes: set[str],
    ) -> dict[str, Any]:
        args = request.normalized_args()
        command = str(args.get("command") or "")
        tool_workspace = str(args.get("workspace_id") or workspace_id)
        network = bool(args.get("network", request.tool.value == "internet"))
        requested_limits = args.get("limits")
        if isinstance(requested_limits, ToolLimits):
            requested_limits_model = requested_limits
        elif isinstance(requested_limits, dict):
            requested_limits_model = ToolLimits(**requested_limits)
        else:
            requested_limits_model = None
        limits = DEFAULT_TOOL_POLICY.effective_limits(request.tool, requested_limits_model)
        digest = tool_call_hash(
            request.tool.value,
            {
                "command": command,
                "workspace_id": tool_workspace,
                "network": network,
                "limits": limits.model_dump(mode="json"),
            },
        )
        if digest in repeated_tool_hashes:
            return {"status": "blocked", "error": "duplicate tool call blocked", "tool": request.tool.value}
        repeated_tool_hashes.add(digest)

        with SessionLocal() as session:
            task = session.get(TaskRecord, task_id)
            if task:
                task.status = TaskStatus.running_tool.value
                task.current_phase = f"running_tool:{subtask_id}"
            _audit(
                session,
                "tool_requested",
                task_id,
                {"category": "tool", "subtask_id": subtask_id, "tool": request.tool.value, "reason": request.reason},
            )
            session.commit()

        job = ToolJobRequest(
            task_id=task_id,
            subtask_id=subtask_id,
            tool=request.tool,
            workspace_id=tool_workspace,
            command=command,
            network=network,
            limits=limits,
        )
        reqs = tool_requirements(request.tool, network=network)
        job_row_id, node = create_reserved_job(task_id, reqs, subtask_id)
        if node is None:
            raise RuntimeError(f"no node available for tool {request.tool.value}")
        attach_execution_context(job, job_row_id)
        lock_type = "read" if request.tool.value in {"internet", "analysis"} else "write"
        if not acquire_workspace_lock(tool_workspace, lock_type, job_row_id):
            blocked_result = ToolJobResult(
                job_id=job.job_id,
                attempt_id=job.attempt_id,
                generation=job.generation,
                node_id=node.id,
                status="blocked",
                duration_ms=0,
                error="workspace lock unavailable",
            )
            _finish_job_or_raise(job_row_id, blocked_result, "failed", task_id, "workspace_lock")
            return blocked_result.model_dump(mode="json")
        try:
            result = self.tool_client.run_tool(job, base_url=node.base_url)
            _finish_job_or_raise(job_row_id, result, _job_terminal_status(result.status), task_id, "tool")
        finally:
            release_workspace_lock(job_row_id, tool_workspace)
        result_payload = result.model_dump(mode="json")
        with SessionLocal() as session:
            task = session.get(TaskRecord, task_id)
            if task and node:
                assigned = json.loads(task.assigned_nodes or "[]")
                if node.id not in assigned:
                    assigned.append(node.id)
                task.assigned_nodes = json.dumps(assigned)
            session.add(
                ToolRunRecord(
                    id=job.job_id,
                    task_id=task_id,
                    job_id=job_row_id,
                    subtask_id=subtask_id,
                    node_id=result.node_id if result.node_id != "unknown" else (node.id if node else None),
                    tool_type=request.tool.value,
                    command=command,
                    network=1 if network else 0,
                    cpus=limits.cpus,
                    memory=limits.memory,
                    timeout_seconds=limits.timeout_seconds,
                    output_limit_mb=limits.output_limit_mb,
                    status=result.status,
                    exit_code=result.exit_code,
                    stdout_excerpt=result.stdout[-4000:],
                    stderr_excerpt=result.stderr[-4000:],
                    error=result.error,
                    error_code=_tool_error_code(result.status),
                    correlation_id=(task.correlation_id if task else task_id),
                    started_at=None,
                    finished_at=None,
                    duration_ms=result.duration_ms,
                )
            )
            _audit(
                session,
                "tool_finished",
                task_id,
                {
                    "category": "tool",
                    "subtask_id": subtask_id,
                    "job_id": job_row_id,
                    "tool_run_id": job.job_id,
                    "tool": request.tool.value,
                    "status": result.status,
                    "exit_code": result.exit_code,
                    "node_id": result.node_id,
                    "duration_ms": result.duration_ms,
                    "error_code": _tool_error_code(result.status),
                    "limits": limits.model_dump(mode="json"),
                },
            )
            session.commit()
        return result_payload

    def _evaluate(
        self, task_id: str, goal: str, workspace_id: str, mode: TaskMode, results: list[dict[str, Any]]
    ) -> dict[str, Any]:
        with SessionLocal() as session:
            task = session.get(TaskRecord, task_id)
            assert task is not None
            task.status = TaskStatus.evaluating.value
            task.current_phase = "evaluating"
            session.commit()

        request = AgentJobRequest(
            task_id=task_id,
            role=AgentRole.smart_agent,
            workspace_id=workspace_id,
            messages=[
                AgentMessage(role="system", content=EVALUATOR_PROMPT),
                AgentMessage(role="user", content=json.dumps({"goal": goal, "results": results}, default=str)),
            ],
        )
        reqs = role_requirements(AgentRole.smart_agent, mode)
        job_id, node = create_reserved_job(task_id, reqs, job_type="smart_evaluation")
        if node is None:
            raise RuntimeError("no node available for evaluation")
        attach_execution_context(request, job_id)
        agent_result = self.model_client.run_agent(request, base_url=node.base_url)
        _finish_job_or_raise(job_id, agent_result, _job_terminal_status(agent_result.status), task_id, "agent")
        if agent_result.status == "ok" and agent_result.response:
            parsed = _parse_json_object(agent_result.response.content)
            if parsed and "passed" in parsed:
                return parsed
        passed = bool(results) and all(r.get("success") for r in results)
        return {
            "passed": passed,
            "score": 80 if passed else 40,
            "missing": [] if passed else ["One or more subtasks failed."],
            "problems": [] if passed else ["Evaluator fallback could not confirm success."],
            "next_action": "finish" if passed else "retry",
        }

    def _optimize_result(
        self,
        task_id: str,
        goal: str,
        workspace_id: str,
        mode: TaskMode,
        results: list[dict[str, Any]],
        evaluation: dict[str, Any],
        base_result: str,
    ) -> dict[str, Any]:
        optimized = {
            "changed": False,
            "score_after": evaluation.get("score", 100),
            "summary": "Optimization skipped or no changes required.",
            "result": base_result,
        }
        rounds = max(0, settings.max_optimization_rounds)
        for round_index in range(1, rounds + 1):
            with SessionLocal() as session:
                task = session.get(TaskRecord, task_id)
                if task:
                    task.status = TaskStatus.optimizing.value
                    task.current_phase = f"optimizing:{round_index}"
                record = OptimizationRoundRecord(
                    id=new_id("opt"),
                    task_id=task_id,
                    round_index=round_index,
                    status="running",
                    score_before=int(optimized.get("score_after") or evaluation.get("score", 0) or 0),
                    summary="",
                )
                session.add(record)
                _audit(session, "optimization_started", task_id, {"round": round_index})
                session.commit()
                opt_id = record.id

            request = AgentJobRequest(
                task_id=task_id,
                role=AgentRole.smart_agent,
                workspace_id=workspace_id,
                messages=[
                    AgentMessage(role="system", content=OPTIMIZER_PROMPT),
                    AgentMessage(
                        role="user",
                        content=json.dumps(
                            {
                                "goal": goal,
                                "evaluation": evaluation,
                                "current_result": optimized.get("result", base_result),
                                "subtask_results": results,
                                "round": round_index,
                            },
                            default=str,
                        ),
                    ),
                ],
            )
            reqs = role_requirements(AgentRole.smart_agent, mode)
            job_id, node = create_reserved_job(task_id, reqs, job_type="smart_optimization")
            if node is None:
                raise RuntimeError("no node available for optimization")
            attach_execution_context(request, job_id)
            agent_result = self.model_client.run_agent(request, base_url=node.base_url)
            _finish_job_or_raise(job_id, agent_result, _job_terminal_status(agent_result.status), task_id, "agent")
            parsed = (
                _parse_json_object(agent_result.response.content)
                if agent_result.status == "ok" and agent_result.response
                else None
            )
            if parsed:
                optimized.update(
                    {
                        "changed": bool(parsed.get("changed", optimized.get("changed", False))),
                        "score_after": int(
                            parsed.get("score_after") or optimized.get("score_after") or evaluation.get("score", 100)
                        ),
                        "summary": str(parsed.get("summary") or optimized.get("summary") or ""),
                        "result": str(parsed.get("result") or optimized.get("result") or base_result),
                    }
                )
                status = "done"
            else:
                status = "skipped"

            with SessionLocal() as session:
                record = session.get(OptimizationRoundRecord, opt_id)
                if record:
                    record.status = status
                    record.score_after = int(optimized.get("score_after") or 0)
                    record.summary = str(optimized.get("summary") or "")[:4000]
                _audit(
                    session,
                    "optimization_finished",
                    task_id,
                    {"round": round_index, "status": status, "score_after": optimized.get("score_after")},
                )
                session.commit()
            if not optimized.get("changed"):
                break
        return optimized

    def _persist_artifacts(self, task_id: str, workspace_id: str) -> list[dict[str, Any]]:
        records = collect_workspace_artifacts(task_id, workspace_id)
        artifacts = [
            {"name": r["name"], "uri": r["uri"], "size_bytes": r["size_bytes"], "sha256": r["sha256"]} for r in records
        ]
        with SessionLocal() as session:
            existing = session.query(ArtifactRecord).filter(ArtifactRecord.task_id == task_id).all()
            for row in existing:
                session.delete(row)
            for item in records:
                session.add(ArtifactRecord(**item))
            _audit(
                session,
                "artifacts_persisted",
                task_id,
                {"category": "artifact", "count": len(artifacts), "artifacts": artifacts},
            )
            session.commit()
        if artifacts:
            return artifacts
        return [{"name": "result.md", "uri": f"workspace://{workspace_id}/output/result.md"}]

    def _final_check(
        self,
        task_id: str,
        goal: str,
        workspace_id: str,
        mode: TaskMode,
        final_result: str,
        artifacts: list[dict[str, Any]],
        optimized: dict[str, Any],
        results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with SessionLocal() as session:
            task = session.get(TaskRecord, task_id)
            assert task is not None
            task.status = TaskStatus.final_check.value
            task.current_phase = "final_check"
            criteria = _task_success_criteria(task)
            _audit(
                session,
                "final_check_started",
                task_id,
                {"category": "verification", "artifact_count": len(artifacts), "success_criteria": criteria},
            )
            session.commit()

        if not settings.require_final_verification:
            result = unavailable(
                "final verification is disabled by configuration", vtype="final_check", verifier_used="configuration"
            ).model_dump()
            with SessionLocal() as session:
                _audit(session, "final_check_finished", task_id, {**result, "category": "verification"})
                session.commit()
            return result

        specs = criteria_to_specs(criteria, subtasks=results)
        if specs and _has_explicit_success_criteria(criteria):
            deterministic = verify_all(specs, workspace_id=workspace_id, text_result=final_result, artifacts=artifacts)
            with SessionLocal() as session:
                _audit(session, "final_check_finished", task_id, {**deterministic, "category": "verification"})
                session.commit()
            return deterministic

        # If no task-level verifier is available, allow an explicit evaluator approval
        # to count as evidence. Do not fall back to non-empty final text.
        request = AgentJobRequest(
            task_id=task_id,
            role=AgentRole.smart_agent,
            workspace_id=workspace_id,
            messages=[
                AgentMessage(role="system", content=FINAL_CHECK_PROMPT),
                AgentMessage(
                    role="user",
                    content=json.dumps(
                        {
                            "goal": goal,
                            "final_result": final_result,
                            "artifacts": artifacts,
                            "optimization": optimized,
                            "subtask_results": results,
                            "required_evidence": [
                                "evaluator approved result",
                                "no failed subtasks",
                            ],
                            "instruction": "Return JSON with passed true only if explicit success evidence is present. Return status unverified when evidence is insufficient.",
                        },
                        default=str,
                    ),
                ),
            ],
        )
        reqs = role_requirements(AgentRole.smart_agent, mode)
        job_id, node = create_reserved_job(task_id, reqs, job_type="final_check")
        if node is None:
            raise RuntimeError("no node available for final_check")
        attach_execution_context(request, job_id)
        agent_result = self.model_client.run_agent(request, base_url=node.base_url)
        _finish_job_or_raise(job_id, agent_result, _job_terminal_status(agent_result.status), task_id, "agent")
        if agent_result.status == "ok" and agent_result.response:
            parsed = _parse_json_object(agent_result.response.content)
            if parsed and ("passed" in parsed or parsed.get("status") in {"passed", "failed", "unverified"}):
                passed = parsed.get("passed")
                status = str(
                    parsed.get("status")
                    or ("passed" if passed is True else "failed" if passed is False else "unverified")
                )
                if passed is not True and status == "passed":
                    passed = True
                if status not in {"passed", "failed", "unverified"}:
                    status = "unverified"
                    passed = None
                normalized = {
                    "verifier_used": str(parsed.get("verifier_used") or AgentRole.smart_agent.value),
                    "status": status,
                    "passed": True if passed is True else False if passed is False else None,
                    "verified": True if passed is True else False if passed is False else None,
                    "explanation": str(
                        parsed.get("explanation")
                        or parsed.get("details")
                        or "final evaluator returned a verification decision"
                    ),
                    "timestamp": _utcnow().isoformat(),
                    "evidence": parsed.get("evidence") if isinstance(parsed.get("evidence"), dict) else {"raw": parsed},
                    "score": parsed.get("score", optimized.get("score_after")),
                    "missing": parsed.get("missing", []),
                    "problems": parsed.get("problems", []),
                    "next_action": parsed.get(
                        "next_action",
                        "finish" if passed is True else "refine" if passed is False else "manual_verification",
                    ),
                }
                with SessionLocal() as session:
                    _audit(session, "final_check_finished", task_id, {**normalized, "category": "verification"})
                    session.commit()
                return normalized

        fallback = unavailable(
            "no explicit success evidence was available; non-empty output is insufficient",
            vtype="final_check",
            verifier_used="fallback",
        ).model_dump()
        with SessionLocal() as session:
            _audit(session, "final_check_finished", task_id, {**fallback, "category": "verification"})
            session.commit()
        return fallback

    def _format_final_result(self, goal: str, results: list[dict[str, Any]], evaluation: dict[str, Any]) -> str:
        summary = [f"Goal: {goal}", "", f"Evaluation score: {evaluation.get('score', 100)}", "", "Subtask results:"]
        for item in results:
            summary.append(
                f"- {item.get('subtask_id')}: success={item.get('success')} executor={item.get('executor')} "
                f"requested={item.get('requested_executor')} reason={item.get('routing_reason')}"
            )
            if item.get("smart_review"):
                summary.append(f"  Smart review: {json.dumps(item.get('smart_review'), default=str)[:500]}")
            content = str(item.get("content") or "").strip()
            if content:
                summary.append(f"  {content[:1000]}")
        return "\n".join(summary)
