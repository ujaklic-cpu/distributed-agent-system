from __future__ import annotations

import json
import shlex
from pathlib import PurePosixPath
from typing import Any

from pydantic import ValidationError

from shared.schemas import ToolRequest


def _safe_file_name(value: str) -> str | None:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or path == PurePosixPath("."):
        return None
    return value


def _write_command(file_name: str, content: str) -> str:
    return f"printf %s {shlex.quote(content)} > {shlex.quote(file_name)}"


def _find_json(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(text[start : end + 1])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    return None


def _build_from_action(parsed: dict[str, Any]) -> list[ToolRequest]:
    action = str(parsed.get("action") or parsed.get("operation") or "").lower()
    tool = str(parsed.get("tool") or "").lower()
    command = str(parsed.get("command") or parsed.get("cmd") or "")
    workspace_id = str(parsed.get("workspace_id") or "default")
    file_name = str(parsed.get("file_name") or parsed.get("filename") or parsed.get("file") or "")
    file_content = str(parsed.get("content") or parsed.get("text") or "")

    if tool in ("shell", "filesystem", "test", "build", "analysis", "internet"):
        args = {"command": command, "workspace_id": workspace_id}
        if command:
            try:
                return [ToolRequest(tool=tool, args=args, reason=parsed.get("reason", f"Execute {tool}"))]
            except ValidationError:
                pass
        safe_name = _safe_file_name(file_name)
        if safe_name and file_content:
            args["command"] = _write_command(safe_name, file_content)
            try:
                return [ToolRequest(tool=tool, args=args, reason=parsed.get("reason", f"Write {file_name}"))]
            except ValidationError:
                pass

    if "create_file" in action or "write" in action:
        safe_name = _safe_file_name(file_name)
        if safe_name:
            try:
                return [
                    ToolRequest(
                        tool="filesystem",
                        args={"command": _write_command(safe_name, file_content), "workspace_id": workspace_id},
                        reason=f"Write {safe_name}",
                    )
                ]
            except ValidationError:
                pass

    if ("run" in action or "execute" in action) and command:
        try:
            return [
                ToolRequest(
                    tool="shell",
                    args={"command": command, "workspace_id": workspace_id},
                    reason=parsed.get("reason", "Execute command"),
                )
            ]
        except ValidationError:
            pass

    if command:
        try:
            return [
                ToolRequest(
                    tool="shell",
                    args={"command": command, "workspace_id": workspace_id},
                    reason=parsed.get("reason", "Execute command"),
                )
            ]
        except ValidationError:
            pass

    safe_name = _safe_file_name(file_name)
    if safe_name:
        try:
            return [
                ToolRequest(
                    tool="filesystem",
                    args={"command": _write_command(safe_name, file_content), "workspace_id": workspace_id},
                    reason=f"Create {safe_name}",
                )
            ]
        except ValidationError:
            pass

    return []


def parse_tool_requests(text: str) -> list[ToolRequest]:
    parsed = _find_json(text)
    if not parsed:
        return []
    raw_requests = parsed.get("tool_requests") or []
    if isinstance(raw_requests, list) and raw_requests:
        requests: list[ToolRequest] = []
        for raw in raw_requests:
            try:
                requests.append(ToolRequest.model_validate(raw))
            except ValidationError:
                continue
        if requests:
            return requests
    return _build_from_action(parsed)
