from pathlib import Path

import pytest

from shared.policy import DEFAULT_TOOL_POLICY, PolicyViolation
from shared.schemas import ToolContainerResult, ToolJobRequest, ToolLimits, ToolType
from shared.tool_metadata import TOOL_IMAGE_VERSION, default_tool_metadata


def test_step611_tool_metadata_is_versioned_and_complete():
    metadata = default_tool_metadata()
    assert set(metadata) == set(ToolType)
    for tool, item in metadata.items():
        assert item.name == tool
        assert item.version == TOOL_IMAGE_VERSION
        assert item.image.endswith(f":{TOOL_IMAGE_VERSION}")
        assert item.required_mounts == ("/workspace",)
        assert item.timeout_seconds > 0
        assert item.cpus > 0
        assert item.memory


def test_step611_network_policy_is_tool_specific():
    DEFAULT_TOOL_POLICY.validate(
        ToolJobRequest(
            task_id="task",
            tool=ToolType.internet,
            workspace_id="ws",
            command="curl https://example.com",
            network=True,
            limits=default_tool_metadata()[ToolType.internet].limits,
        )
    )
    with pytest.raises(PolicyViolation):
        DEFAULT_TOOL_POLICY.validate(
            ToolJobRequest(
                task_id="task", tool=ToolType.shell, workspace_id="ws", command="curl https://example.com", network=True
            )
        )
    with pytest.raises(PolicyViolation):
        DEFAULT_TOOL_POLICY.validate(
            ToolJobRequest(
                task_id="task",
                tool=ToolType.internet,
                workspace_id="ws",
                command="curl https://example.com",
                network=False,
                limits=default_tool_metadata()[ToolType.internet].limits,
            )
        )


def test_step611_limits_cannot_exceed_tool_metadata():
    with pytest.raises(PolicyViolation):
        DEFAULT_TOOL_POLICY.validate(
            ToolJobRequest(
                task_id="task",
                tool=ToolType.filesystem,
                workspace_id="ws",
                command="find .",
                limits=ToolLimits(cpus=3, memory="1g", timeout_seconds=120, output_limit_mb=10),
            )
        )


def test_step611_tool_output_schema_rejects_arbitrary_payload():
    parsed = ToolContainerResult.model_validate(
        {
            "schema_version": "tool-result.v1",
            "exit_code": 0,
            "stdout": "ok",
            "stderr": "",
            "duration_ms": 1,
            "artifacts": [],
        }
    )
    assert parsed.exit_code == 0
    with pytest.raises(Exception):
        ToolContainerResult.model_validate({"stdout": "ok"})


def test_step611_tool_images_use_common_runner_wrappers():
    root = Path(__file__).resolve().parents[1]
    common = root / "tool-images" / "common_runner.py"
    assert common.exists()
    for runner in (root / "tool-images").glob("*/runner.py"):
        assert "from common_runner import run_tool" in runner.read_text()
        dockerfile = runner.with_name("Dockerfile")
        assert "COPY tool-images/common_runner.py /tool/common_runner.py" in dockerfile.read_text()
