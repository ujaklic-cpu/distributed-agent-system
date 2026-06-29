from pathlib import Path

from agent_server.prompt_runtime import mock_chat
from orchestrator.artifacts import collect_workspace_artifacts
from orchestrator.config import settings
from shared.messages import FINAL_CHECK_PROMPT, OPTIMIZER_PROMPT
from shared.schemas import AgentChatRequest, AgentMessage, AgentRole, TaskStatus


def test_phase5_statuses_exist():
    assert TaskStatus.optimizing.value == "optimizing"
    assert TaskStatus.final_check.value == "final_check"


def test_mock_optimizer_and_final_check_contracts():
    optimize = mock_chat(
        AgentChatRequest(
            role=AgentRole.smart_agent,
            workspace_id="demo",
            messages=[
                AgentMessage(role="system", content=OPTIMIZER_PROMPT),
                AgentMessage(role="user", content='{"passed": true}'),
            ],
        )
    )
    assert "score_after" in optimize.content

    final = mock_chat(
        AgentChatRequest(
            role=AgentRole.smart_agent,
            workspace_id="demo",
            messages=[
                AgentMessage(role="system", content=FINAL_CHECK_PROMPT),
                AgentMessage(role="user", content=optimize.content),
            ],
        )
    )
    assert '"passed": true' in final.content


def test_collect_workspace_artifacts_copies_and_hashes(tmp_path):
    old_workspace_root = settings.orchestrator_workspace_root
    old_artifact_root = settings.artifact_storage_root
    try:
        settings.orchestrator_workspace_root = str(tmp_path / "workspaces")
        settings.artifact_storage_root = str(tmp_path / "artifact-store")
        workspace = Path(settings.orchestrator_workspace_root) / "demo" / "output"
        workspace.mkdir(parents=True)
        (workspace / "result.md").write_text("ok", encoding="utf-8")

        records = collect_workspace_artifacts("task_1", "demo")
        assert len(records) == 1
        assert records[0]["name"] == "output/result.md"
        assert records[0]["size_bytes"] == 2
        assert len(records[0]["sha256"]) == 64
        assert Path(records[0]["stored_path"]).exists()
    finally:
        settings.orchestrator_workspace_root = old_workspace_root
        settings.artifact_storage_root = old_artifact_root
