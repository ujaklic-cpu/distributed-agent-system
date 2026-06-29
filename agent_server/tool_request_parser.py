from __future__ import annotations

import json
import re
from typing import Any

from shared.schemas import ToolRequest


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
    workspace_id = str(parsed.get("workspace_id") or parsed.get("path") or "default")
    file_name = str(parsed.get("file_name") or parsed.get("filename") or parsed.get("file") or "")
    file_content = str(parsed.get("content") or parsed.get("text") or "")

    if tool in ("shell", "filesystem", "test", "build", "analysis", "internet"):
        args = {"command": command, "workspace_id": workspace_id}
        if command:
            try:
                return [ToolRequest(tool=tool, args=args, reason=parsed.get("reason", f"Execute {tool}"))]
            except Exception:
                pass
        if file_name and file_content:
            args["command"] = (f"cat > {file_name} << 'EOF'\n{file_content}\nEOF" if "\n" in file_content
                               else f"echo '{file_content}' > {file_name}")
            try:
                return [ToolRequest(tool=tool, args=args, reason=parsed.get("reason", f"Write {file_name}"))]
            except Exception:
                pass

    if "create_file" in action or "write" in action:
        cmd = (f"cat > {file_name} << 'EOF'\n{file_content}\nEOF" if "\n" in file_content
               else f"echo '{file_content}' > {file_name}")
        if file_name and cmd:
            try:
                return [ToolRequest(tool="filesystem", args={"command": cmd, "workspace_id": workspace_id},
                                    reason=f"Write {file_name}")]
            except Exception:
                pass

    if "run" in action or "execute" in action:
        if command:
            try:
                return [ToolRequest(tool="shell", args={"command": command, "workspace_id": workspace_id},
                                    reason=parsed.get("reason", "Execute command"))]
            except Exception:
                pass

    if command:
        try:
            return [ToolRequest(tool="shell", args={"command": command, "workspace_id": workspace_id},
                                reason=parsed.get("reason", "Execute command"))]
        except Exception:
            pass

    if file_name:
        cmd = f"echo 'Hello from AI' > {file_name}" if not file_content else f"echo '{file_content}' > {file_name}"
        try:
            return [ToolRequest(tool="filesystem", args={"command": cmd, "workspace_id": workspace_id},
                                reason=f"Create {file_name}")]
        except Exception:
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
            except Exception:
                continue
        if requests:
            return requests
    return _build_from_action(parsed)
