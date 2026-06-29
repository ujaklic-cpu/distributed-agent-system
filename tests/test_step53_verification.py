from __future__ import annotations

from orchestrator import verification


def test_text_and_json_schema_verification():
    assert verification.verify({"type": "text_contains", "contains": "done"}, text_result="done").passed
    assert verification.verify(
        {"type": "json_schema", "schema": {"type": "object", "required": ["ok"]}}, text_result='{"ok": true}'
    ).passed
    assert not verification.verify(
        {"type": "json_schema", "schema": {"type": "object", "required": ["ok"]}}, text_result='{"missing": true}'
    ).passed


def test_file_exists_and_command_verification(tmp_path, monkeypatch):
    monkeypatch.setattr(verification.settings, "orchestrator_workspace_root", str(tmp_path))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "result.txt").write_text("ok", encoding="utf-8")

    assert verification.verify({"type": "file_exists", "path": "result.txt"}, workspace_id="ws").passed
    assert verification.verify({"type": "command_exit_zero", "command": "test -f result.txt"}, workspace_id="ws").passed
    assert not verification.verify(
        {"type": "command_exit_zero", "command": "test -f missing.txt"}, workspace_id="ws"
    ).passed


def test_artifact_created_verification():
    artifacts = [{"name": "result.md"}]
    assert verification.verify({"type": "artifact_created", "name": "result.md"}, artifacts=artifacts).passed
    assert not verification.verify({"type": "artifact_created", "name": "other.md"}, artifacts=artifacts).passed
