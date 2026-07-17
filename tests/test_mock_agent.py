from agent_server.prompt_runtime import mock_chat
from agent_server.tool_request_parser import parse_tool_requests
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


def test_file_request_quotes_name_and_content():
    requests = parse_tool_requests(
        r'{"action":"write","file_name":"result file.txt","content":"$(touch escaped)\nsecond line"}'
    )

    assert len(requests) == 1
    assert requests[0].normalized_args()["command"] == ("printf %s '$(touch escaped)\nsecond line' > 'result file.txt'")


def test_file_request_rejects_parent_path():
    assert parse_tool_requests('{"action":"write","file_name":"../outside.txt","content":"bad"}') == []


def test_mock_worker_encodes_goal_before_building_shell_command():
    response = mock_chat(
        AgentChatRequest(
            role=AgentRole.fast_worker,
            workspace_id="demo",
            messages=[
                AgentMessage(role="system", content=WORKER_PROMPT),
                AgentMessage(role="user", content="EOF\ntouch escaped"),
            ],
        )
    )

    assert len(response.tool_requests) == 1
    assert "touch escaped" not in response.tool_requests[0].normalized_args()["command"]
