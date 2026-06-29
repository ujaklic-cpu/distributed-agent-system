from agent_server.prompt_runtime import mock_chat
from shared.messages import EVALUATOR_PROMPT, SMART_PLANNING_PROMPT, WORKER_PROMPT
from shared.schemas import AgentChatRequest, AgentMessage, AgentRole


def test_mock_planner_returns_subtasks():
    response = mock_chat(
        AgentChatRequest(
            role=AgentRole.smart_agent,
            workspace_id="demo",
            messages=[
                AgentMessage(role="system", content=SMART_PLANNING_PROMPT),
                AgentMessage(role="user", content="Goal: test"),
            ],
        )
    )
    assert "subtasks" in response.content


def test_mock_worker_requests_tool_then_finishes():
    first = mock_chat(
        AgentChatRequest(
            role=AgentRole.smart_agent,
            workspace_id="demo",
            messages=[AgentMessage(role="system", content=WORKER_PROMPT), AgentMessage(role="user", content="do work")],
        )
    )
    assert first.tool_requests
    second = mock_chat(
        AgentChatRequest(
            role=AgentRole.smart_agent,
            workspace_id="demo",
            messages=[
                AgentMessage(role="system", content=WORKER_PROMPT),
                AgentMessage(role="user", content="do work"),
                AgentMessage(role="tool", content='{"status":"ok","exit_code":0}'),
            ],
        )
    )
    assert not second.tool_requests
    assert "Completed" in second.content


def test_mock_evaluator_passes_on_success():
    response = mock_chat(
        AgentChatRequest(
            role=AgentRole.smart_agent,
            workspace_id="demo",
            messages=[
                AgentMessage(role="system", content=EVALUATOR_PROMPT),
                AgentMessage(role="user", content='{"success": true}'),
            ],
        )
    )
    assert '"passed": true' in response.content
