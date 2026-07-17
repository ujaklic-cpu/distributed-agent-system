from orchestrator import verification


def test_non_empty_text_is_unverified_not_passed():
    result = verification.verify({"type": "text_result", "min_length": 3}, text_result="done")
    assert result.passed is None
    assert result.status == "unverified"
    assert "not explicit success evidence" in result.explanation


def test_required_files_commands_and_output_type_are_composite_success(tmp_path, monkeypatch):
    monkeypatch.setattr(verification.settings, "orchestrator_workspace_root", str(tmp_path))
    ws = tmp_path / "ws66"
    ws.mkdir()
    (ws / "result.json").write_text('{"ok": true}')
    criteria = {
        "required_files": ["result.json"],
        "required_commands": ["test -f result.json"],
        "expected_output_type": "json",
        "verification_command": "python3 -m json.tool result.json >/dev/null",
    }
    specs = verification.criteria_to_specs(criteria, subtasks=[{"subtask_id": "T1", "success": True}])
    result = verification.verify_all(specs, workspace_id="ws66", text_result='{"ok": true}', artifacts=[])
    assert result["status"] == "passed"
    assert result["passed"] is True
    assert len(result["evidence"]["checks"]) == 5


def test_composite_verification_tracks_failed_and_unverified(tmp_path, monkeypatch):
    monkeypatch.setattr(verification.settings, "orchestrator_workspace_root", str(tmp_path))
    (tmp_path / "ws66").mkdir()
    failed = verification.verify_all([{"type": "file_exists", "path": "missing.txt"}], workspace_id="ws66")
    assert failed["status"] == "failed"
    assert failed["passed"] is False

    unverified = verification.verify_all([{"type": "unavailable"}], workspace_id="ws66")
    assert unverified["status"] == "unverified"
    assert unverified["passed"] is None
