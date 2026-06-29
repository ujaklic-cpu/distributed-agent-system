from __future__ import annotations

import hashlib
import json
from typing import Any


def stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def plan_hash(plan: dict[str, Any]) -> str:
    return stable_hash(plan)


def tool_call_hash(tool_name: str, args: dict[str, Any]) -> str:
    return stable_hash({"tool": tool_name, "args": args})
