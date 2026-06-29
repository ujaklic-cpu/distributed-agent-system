from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

WORKSPACE = Path("/workspace")
MAX_INLINE_BYTES = int(os.environ.get("TOOL_INLINE_OUTPUT_BYTES", "65536"))
MAX_RESULT_BYTES = int(os.environ.get("TOOL_MAX_RESULT_BYTES", "1048576"))


def _collect_artifacts() -> list[dict[str, str | int]]:
    artifacts_dir = WORKSPACE / "artifacts"
    if not artifacts_dir.exists():
        return []
    artifacts: list[dict[str, str | int]] = []
    for path in artifacts_dir.rglob("*"):
        if path.is_file():
            try:
                rel = path.relative_to(WORKSPACE).as_posix()
            except ValueError:
                continue
            artifacts.append({"name": path.name, "path": rel, "size_bytes": path.stat().st_size})
    return artifacts[:200]


def _summary(text: str, limit: int = MAX_INLINE_BYTES) -> str:
    data = text.encode("utf-8", errors="replace")
    if len(data) <= limit:
        return text
    head = data[: limit // 2].decode("utf-8", errors="replace")
    tail = data[-limit // 2 :].decode("utf-8", errors="replace")
    return f"{head}\n... [truncated {len(data) - limit} bytes] ...\n{tail}"


def _write_full_log(name: str, text: str) -> dict[str, str | int] | None:
    data = text.encode("utf-8", errors="replace")
    if len(data) <= MAX_INLINE_BYTES:
        return None
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)
    logs_dir = WORKSPACE / "artifacts" / "tool-logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / safe
    path.write_text(text, encoding="utf-8")
    return {"name": path.name, "path": path.relative_to(WORKSPACE).as_posix(), "size_bytes": path.stat().st_size}


def _write_result(result_path: Path, payload: dict[str, Any]) -> None:
    result_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, default=str)
    if len(rendered.encode("utf-8")) > MAX_RESULT_BYTES:
        payload = dict(payload)
        payload["stdout"] = _summary(str(payload.get("stdout", "")), MAX_INLINE_BYTES // 2)
        payload["stderr"] = _summary(str(payload.get("stderr", "")), MAX_INLINE_BYTES // 2)
        payload["result_truncated"] = True
        rendered = json.dumps(payload, default=str)
    result_path.write_text(rendered, encoding="utf-8")


def run_tool() -> int:
    command = os.environ.get("TOOL_COMMAND", "")
    timeout = int(os.environ.get("TOOL_TIMEOUT_SECONDS", "300"))
    result_path = Path(os.environ.get("TOOL_RESULT_PATH", "/workspace/.tool-results/result.json"))
    tool_name = os.environ.get("TOOL_NAME", "tool")
    start = time.monotonic()
    try:
        completed = subprocess.run(
            ["bash", "-lc", command],
            cwd=str(WORKSPACE),
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        stdout_artifact = _write_full_log(f"{tool_name}-{result_path.stem}.stdout.log", completed.stdout)
        stderr_artifact = _write_full_log(f"{tool_name}-{result_path.stem}.stderr.log", completed.stderr)
        artifacts = _collect_artifacts()
        for artifact in (stdout_artifact, stderr_artifact):
            if artifact is not None:
                artifacts.append(artifact)
        payload = {
            "schema_version": "tool-result.v1",
            "exit_code": completed.returncode,
            "stdout": _summary(completed.stdout),
            "stderr": _summary(completed.stderr),
            "duration_ms": int((time.monotonic() - start) * 1000),
            "artifacts": artifacts,
            "stdout_truncated": stdout_artifact is not None,
            "stderr_truncated": stderr_artifact is not None,
        }
        _write_result(result_path, payload)
        return completed.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        stdout_artifact = _write_full_log(f"{tool_name}-{result_path.stem}.stdout.log", stdout)
        stderr_artifact = _write_full_log(f"{tool_name}-{result_path.stem}.stderr.log", stderr)
        artifacts = _collect_artifacts()
        for artifact in (stdout_artifact, stderr_artifact):
            if artifact is not None:
                artifacts.append(artifact)
        payload = {
            "schema_version": "tool-result.v1",
            "exit_code": 124,
            "stdout": _summary(stdout),
            "stderr": _summary(stderr),
            "duration_ms": int((time.monotonic() - start) * 1000),
            "artifacts": artifacts,
            "error": {"code": "TOOL_TIMEOUT", "message": "tool command timed out", "details": {}},
            "stdout_truncated": stdout_artifact is not None,
            "stderr_truncated": stderr_artifact is not None,
        }
        _write_result(result_path, payload)
        return 124


if __name__ == "__main__":
    raise SystemExit(run_tool())
