from __future__ import annotations

import os
import shutil
import subprocess

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_DOCKER_INTEGRATION_TESTS") != "1",
        reason="set RUN_DOCKER_INTEGRATION_TESTS=1 to run real Docker cluster tests",
    ),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker CLI is not installed"),
]


def test_control_plane_compose_file_starts() -> None:
    completed = subprocess.run(
        ["docker", "compose", "-f", "deploy/docker-compose.control.yml", "config"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "CLUSTER_TOKEN" in completed.stdout
    assert "TOOL_CONTAINER_AUTO_REMOVE" in completed.stdout
