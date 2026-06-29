from shared.policy import DEFAULT_TOOL_POLICY, PolicyViolation
from shared.schemas import ToolJobRequest, ToolType


def test_shell_without_network_allowed():
    job = ToolJobRequest(task_id="task_1", tool=ToolType.shell, workspace_id="demo", command="echo ok")
    DEFAULT_TOOL_POLICY.validate(job)


def test_network_shell_blocked():
    job = ToolJobRequest(task_id="task_1", tool=ToolType.shell, workspace_id="demo", command="echo ok", network=True)
    try:
        DEFAULT_TOOL_POLICY.validate(job)
    except PolicyViolation:
        return
    raise AssertionError("expected PolicyViolation")


def test_bad_workspace_blocked():
    job = ToolJobRequest(task_id="task_1", tool=ToolType.shell, workspace_id="../host", command="echo ok")
    try:
        DEFAULT_TOOL_POLICY.validate(job)
    except Exception:
        return
    raise AssertionError("expected validation or policy failure")
