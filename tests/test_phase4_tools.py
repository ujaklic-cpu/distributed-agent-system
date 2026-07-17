from pathlib import Path

from node_agent.config import settings
from node_agent.docker_manager import DockerToolRunner, _resolve_host_workspace
from orchestrator.state_machine import TaskRunner
from shared.policy import DEFAULT_TOOL_POLICY, PolicyViolation
from shared.schemas import ToolJobRequest, ToolRequest, ToolType


def test_internet_tool_requires_network_flag():
    job = ToolJobRequest(
        task_id="task_1", tool=ToolType.internet, workspace_id="demo", command="curl https://example.com"
    )
    try:
        DEFAULT_TOOL_POLICY.validate(job)
    except PolicyViolation as exc:
        assert "network=true" in str(exc)
        return
    raise AssertionError("expected PolicyViolation")


def test_internet_tool_with_network_is_allowed():
    job = ToolJobRequest(
        task_id="task_1", tool=ToolType.internet, workspace_id="demo", command="curl https://example.com", network=True
    )
    DEFAULT_TOOL_POLICY.validate(job)


def test_per_tool_default_limits_are_distinct():
    shell = DEFAULT_TOOL_POLICY.effective_limits(ToolType.shell)
    build = DEFAULT_TOOL_POLICY.effective_limits(ToolType.build)
    filesystem = DEFAULT_TOOL_POLICY.effective_limits(ToolType.filesystem)
    assert build.cpus > shell.cpus
    assert filesystem.timeout_seconds < shell.timeout_seconds


def test_docker_runner_maps_all_tool_images():
    runner = DockerToolRunner()
    assert runner.image_for_tool(ToolType.shell) == settings.tool_image_shell
    assert runner.image_for_tool(ToolType.test) == settings.tool_image_test
    assert runner.image_for_tool(ToolType.internet) == settings.tool_image_internet
    assert runner.image_for_tool(ToolType.filesystem) == settings.tool_image_filesystem
    assert runner.image_for_tool(ToolType.build) == settings.tool_image_build
    assert runner.image_for_tool(ToolType.analysis) == settings.tool_image_analysis


def test_tool_container_hardening_defaults():
    assert settings.tool_container_read_only_root is True
    assert settings.tool_container_tmpfs is True
    assert settings.tool_container_user == "1000:1000"


def test_docker_runner_uses_workspace_as_only_writable_mount(monkeypatch):
    monkeypatch.setattr(settings, "node_workspace_root", "/workspaces")
    monkeypatch.setattr(settings, "tool_container_read_only_root", True)
    monkeypatch.setattr(settings, "tool_container_tmpfs", True)
    monkeypatch.setattr(settings, "tool_container_user", "1000:1000")
    runner = DockerToolRunner.__new__(DockerToolRunner)
    job = ToolJobRequest(task_id="task_1", tool=ToolType.shell, workspace_id="demo", command="echo ok")

    options = runner._container_run_options(
        job,
        "local-agent/tool-shell:0.7.0",
        "/host/workspaces/demo",
        Path("/workspaces/demo/.tool-results/result.json"),
    )

    assert options["read_only"] is True
    assert options["user"] == "1000:1000"
    assert options["tmpfs"] == {"/tmp": "rw,nosuid,nodev,noexec,size=512m"}
    assert options["volumes"] == {"/host/workspaces/demo": {"bind": "/workspace", "mode": "rw"}}
    assert options["environment"]["TOOL_RESULT_PATH"] == "/workspace/.tool-results/result.json"
    assert options["environment"]["HOME"] == "/tmp"
    assert options["environment"]["PYTHONDONTWRITEBYTECODE"] == "1"


def test_read_only_tool_mounts_only_result_directory_as_writable(monkeypatch):
    monkeypatch.setattr(settings, "node_workspace_root", "/workspaces")
    runner = DockerToolRunner.__new__(DockerToolRunner)
    job = ToolJobRequest(
        task_id="task_1",
        tool=ToolType.analysis,
        workspace_id="demo",
        command="find .",
    )

    options = runner._container_run_options(
        job,
        "local-agent/tool-analysis:0.7.0",
        "/host/workspaces/demo",
        Path("/workspaces/demo/.tool-results/result.json"),
    )

    assert options["volumes"] == {
        "/host/workspaces/demo": {"bind": "/workspace", "mode": "ro"},
        "/host/workspaces/demo/.tool-results": {"bind": "/workspace/.tool-results", "mode": "rw"},
    }


def test_host_workspace_resolution_stays_under_root(monkeypatch, tmp_path):
    root = tmp_path / "workspaces"
    root.mkdir()
    monkeypatch.setattr(settings, "host_workspace_root", str(root))

    workspace = _resolve_host_workspace("demo")

    assert workspace == root.resolve() / "demo"


def test_host_workspace_resolution_rejects_symlink_escape(monkeypatch, tmp_path):
    root = tmp_path / "workspaces"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "demo").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(settings, "host_workspace_root", str(root))

    try:
        _resolve_host_workspace("demo")
    except ValueError as exc:
        assert "escapes host_workspace_root" in str(exc)
        return
    raise AssertionError("expected ValueError")


def test_tool_request_cannot_override_assigned_workspace():
    request = ToolRequest(tool=ToolType.shell, args={"command": "true", "workspace_id": "other"})

    result = TaskRunner()._run_tool_request("task", "assigned", "subtask", request, set())

    assert result["status"] == "blocked"
    assert "assigned workspace" in result["error"]
