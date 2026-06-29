from __future__ import annotations

from dataclasses import dataclass

from shared.schemas import AgentRole, TaskMode

SECURITY_KEYWORDS = (
    "security",
    "docker",
    "container",
    "orchestration",
    "scheduler",
    "state machine",
    "privileged",
    "permission",
    "policy",
    "isolation",
    "sandbox",
)


@dataclass(frozen=True)
class RouteDecision:
    executor: AgentRole
    reason: str
    review_required: bool = False


def clamp_score(value: int | float | str | None, default: int = 5) -> int:
    try:
        score = int(value)  # type: ignore[arg-type]
    except Exception:
        score = default
    return max(1, min(10, score))


def contains_security_sensitive_text(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in SECURITY_KEYWORDS)


def route_subtask(
    *,
    requested_executor: AgentRole | None,
    difficulty: int,
    risk: int,
    description: str,
    success_condition: str = "",
    mode: TaskMode = TaskMode.normal,
    previous_failures: int = 0,
) -> RouteDecision:
    """Apply deterministic Phase 3 routing rules.

    Planner output is treated as advice. High-risk, high-difficulty, failed, or
    security-sensitive work is forced to the smart agent. Medium work goes to a
    code worker with mandatory smart review. Low-risk mechanical work goes to a
    fast worker unless the requested role is more capable.
    """
    difficulty = clamp_score(difficulty)
    risk = clamp_score(risk)
    requested_executor = requested_executor or AgentRole.smart_agent
    text = f"{description}\n{success_condition}"

    if previous_failures >= 2:
        return RouteDecision(AgentRole.smart_agent, "worker failed twice; escalated to smart agent")
    if risk >= 7:
        return RouteDecision(AgentRole.smart_agent, "risk score requires smart agent")
    if difficulty >= 7:
        return RouteDecision(AgentRole.smart_agent, "difficulty score requires smart agent")
    if contains_security_sensitive_text(text):
        return RouteDecision(AgentRole.smart_agent, "security/container/orchestration work requires smart agent")
    if requested_executor == AgentRole.research_worker:
        return RouteDecision(requested_executor, "planner requested specialized executor")
    if mode == TaskMode.cheap and difficulty <= 4 and risk <= 4:
        return RouteDecision(AgentRole.fast_worker, "cheap mode prefers smallest acceptable worker")
    if difficulty <= 3:
        if requested_executor == AgentRole.code_worker:
            return RouteDecision(AgentRole.code_worker, "planner requested code worker for simple code task", True)
        return RouteDecision(AgentRole.fast_worker, "low difficulty routed to fast worker")
    return RouteDecision(AgentRole.code_worker, "medium difficulty routed to code worker", True)


def requires_smart_review(executor: AgentRole, difficulty: int, risk: int, success: bool) -> bool:
    if executor == AgentRole.smart_agent:
        return False
    if not success:
        return False
    if executor == AgentRole.code_worker:
        return True
    return risk >= 5 or difficulty >= 4
