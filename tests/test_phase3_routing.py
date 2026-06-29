from types import SimpleNamespace

from orchestrator.routing import route_subtask
from orchestrator.scheduler import apply_mode_downgrade, role_requirements, score_node
from shared.schemas import AgentRole, TaskMode


def test_low_difficulty_routes_to_fast_worker():
    decision = route_subtask(
        requested_executor=AgentRole.smart_agent, difficulty=2, risk=2, description="Summarize logs"
    )
    assert decision.executor == AgentRole.fast_worker


def test_medium_difficulty_routes_to_code_worker_with_review():
    decision = route_subtask(
        requested_executor=AgentRole.fast_worker, difficulty=5, risk=4, description="Write a small unit test"
    )
    assert decision.executor == AgentRole.code_worker
    assert decision.review_required is True


def test_high_risk_overrides_worker_request_to_smart():
    decision = route_subtask(
        requested_executor=AgentRole.code_worker, difficulty=4, risk=8, description="Patch scheduler retry logic"
    )
    assert decision.executor == AgentRole.smart_agent


def test_security_sensitive_text_routes_to_smart():
    decision = route_subtask(
        requested_executor=AgentRole.fast_worker, difficulty=2, risk=2, description="Change Docker isolation policy"
    )
    assert decision.executor == AgentRole.smart_agent


def test_fast_mode_downgrades_smart_requirement_but_keeps_capability():
    strictish = role_requirements(AgentRole.smart_agent, TaskMode.normal)
    fast = apply_mode_downgrade(strictish, TaskMode.fast)
    assert fast.min_vram_gb < strictish.min_vram_gb
    assert fast.required_capability == "smart_agent"


def test_score_node_prefers_loaded_acceptable_model():
    req = role_requirements(AgentRole.code_worker)
    node = SimpleNamespace(
        status="healthy",
        ram_free_gb=32,
        cpu_free=8,
        gpus=[{"vram_gb_free": 8}],
        capabilities=["code_worker"],
        loaded_models=["qwen2.5-coder:7b"],
        running_jobs=0,
    )
    assert score_node(node, req) > 0
