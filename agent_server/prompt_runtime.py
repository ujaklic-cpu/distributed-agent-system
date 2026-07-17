from __future__ import annotations

import json
from base64 import b64encode
from datetime import UTC, datetime

from shared.schemas import AgentChatRequest, AgentChatResponse, ToolRequest, ToolType


def _last_user_text(request: AgentChatRequest) -> str:
    for message in reversed(request.messages):
        if message.role == "user":
            return message.content
    return ""


def _has_tool_result(request: AgentChatRequest) -> bool:
    return any(message.role == "tool" for message in request.messages)


def mock_chat(request: AgentChatRequest) -> AgentChatResponse:
    system = request.messages[0].content if request.messages else ""
    user_text = _last_user_text(request)
    workspace_id = request.workspace_id or "default"
    goal_lower = user_text.lower()

    if "Plan the task" in system:
        if any(word in goal_lower for word in ["simple", "summarize", "inspect", "list files"]):
            difficulty, risk, executor = 2, 2, "fast_worker"
        elif any(word in goal_lower for word in ["test", "small bug", "format", "fixture"]):
            difficulty, risk, executor = 5, 4, "code_worker"
        elif any(word in goal_lower for word in ["security", "docker", "scheduler", "state machine", "orchestrator"]):
            difficulty, risk, executor = 8, 8, "smart_agent"
        else:
            difficulty, risk, executor = 7, 5, "smart_agent"
        plan = {
            "subtasks": [
                {
                    "id": "T1",
                    "description": "Complete the goal in the assigned workspace and produce an output artifact.",
                    "difficulty": difficulty,
                    "risk": risk,
                    "executor": executor,
                    "success_condition": "The workspace contains output/result.md and the final answer summarizes the completed work.",
                }
            ]
        }
        return AgentChatResponse(content=json.dumps(plan, indent=2))

    if "Smart Agent reviewer" in system:
        review = {
            "passed": '"success": true' in user_text or 'status": "ok' in user_text,
            "score": 90,
            "problems": [],
            "required_fixes": [],
            "next_action": "accept",
        }
        if not review["passed"]:
            review.update(
                {
                    "score": 40,
                    "problems": ["Worker result did not show success."],
                    "required_fixes": ["Complete directly with smart agent."],
                    "next_action": "fix_directly",
                }
            )
        return AgentChatResponse(content=json.dumps(review, indent=2))

    if "Smart Agent optimizer" in system:
        optimized = {
            "changed": True,
            "score_after": 92,
            "summary": "Mock optimizer confirmed the passed result and kept the artifact index stable.",
            "result": "Optimized final result: the workspace artifact is complete and verified.",
        }
        return AgentChatResponse(content=json.dumps(optimized, indent=2))

    if "final verifier" in system.lower():
        passed = (
            "Optimized final result" in user_text
            or '"status": "ok"' in user_text
            or "success=True" in user_text
            or '"success": true' in user_text
        )
        final = {
            "passed": passed,
            "score": 95 if passed else 50,
            "missing": [] if passed else ["No optimized or successful result was found."],
            "problems": [] if passed else ["Final mock check failed."],
            "next_action": "finish" if passed else "refine",
        }
        return AgentChatResponse(content=json.dumps(final, indent=2))

    if "Evaluate whether the result satisfies" in system:
        passed = '"status": "ok"' in user_text or "success=True" in user_text or '"success": true' in user_text
        evaluation = {
            "passed": passed,
            "score": 90 if passed else 45,
            "missing": [] if passed else ["No successful tool or subtask result was found."],
            "problems": [] if passed else ["The mock evaluator could not verify completion."],
            "next_action": "finish" if passed else "retry",
        }
        return AgentChatResponse(content=json.dumps(evaluation, indent=2))

    if not _has_tool_result(request):
        result_text = (
            "# Task result\n\n"
            f"Workspace: {workspace_id}\n"
            f"Generated at: {datetime.now(UTC).isoformat()}\n\n"
            "The assigned task completed and produced this output artifact.\n\n"
            f"Input context:\n{user_text[:4000]}\n"
        )
        encoded_result = b64encode(result_text.encode("utf-8")).decode("ascii")
        command = f"""mkdir -p output logs scratch
printf %s {encoded_result} | base64 -d > output/result.md
python3 - <<'PY'
from pathlib import Path
assert Path('output/result.md').exists()
print(Path('output/result.md').read_text())
PY"""
        tool_request = ToolRequest(
            tool=ToolType.shell,
            args={"command": command, "workspace_id": workspace_id, "network": False},
            reason="Create the result artifact inside the assigned workspace.",
        )
        payload = {
            "content": "I need one shell tool run in the disposable workspace container.",
            "tool_requests": [tool_request.model_dump(mode="json")],
        }
        return AgentChatResponse(content=json.dumps(payload, indent=2), tool_requests=[tool_request])

    return AgentChatResponse(
        content=(
            f"Completed the subtask as {request.role.value}. The tool result confirms the workspace artifact was written. "
            "The final result is available as output/result.md."
        ),
        tool_requests=[],
    )
