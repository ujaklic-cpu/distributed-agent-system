from __future__ import annotations

import re
from dataclasses import dataclass

from shared.schemas import ToolJobRequest, ToolLimits, ToolType
from shared.tool_metadata import default_tool_metadata


class PolicyViolation(Exception):
    pass


_MEMORY_RE = re.compile(r"^[1-9][0-9]*(?:[kKmMgG])?$|^[1-9][0-9]*(?:MiB|GiB|MB|GB)$")


def _memory_bytes(value: str) -> int:
    match = re.fullmatch(r"([1-9][0-9]*)([kKmMgG]|MiB|GiB|MB|GB)?", value)
    if match is None:
        raise PolicyViolation("memory limit has an invalid format")
    amount = int(match.group(1))
    unit = (match.group(2) or "").lower()
    multipliers = {
        "": 1,
        "k": 1_000,
        "m": 1_000_000,
        "g": 1_000_000_000,
        "kb": 1_000,
        "mb": 1_000_000,
        "gb": 1_000_000_000,
        "mib": 1024**2,
        "gib": 1024**3,
    }
    return amount * multipliers[unit]


@dataclass(frozen=True)
class ToolLimitDefaults:
    cpus: float
    memory: str
    timeout_seconds: int
    output_limit_mb: int = 20


TOOL_LIMIT_DEFAULTS: dict[ToolType, ToolLimitDefaults] = {
    tool: ToolLimitDefaults(
        cpus=metadata.cpus,
        memory=metadata.memory,
        timeout_seconds=metadata.timeout_seconds,
        output_limit_mb=metadata.output_limit_mb,
    )
    for tool, metadata in default_tool_metadata().items()
}


@dataclass(frozen=True)
class ToolPolicy:
    allowed_tools: tuple[ToolType, ...] = tuple(default_tool_metadata().keys())
    max_command_length: int = 50000
    safe_workspace_pattern: str = r"[A-Za-z0-9_.-]{1,128}"
    max_cpus: float = 64
    max_timeout_seconds: int = 3600
    max_output_limit_mb: int = 200

    def effective_limits(self, tool: ToolType, requested: ToolLimits | None = None) -> ToolLimits:
        defaults = TOOL_LIMIT_DEFAULTS[tool]
        if requested is None:
            return ToolLimits(
                cpus=defaults.cpus,
                memory=defaults.memory,
                timeout_seconds=defaults.timeout_seconds,
                output_limit_mb=defaults.output_limit_mb,
            )
        return ToolLimits(
            cpus=requested.cpus or defaults.cpus,
            memory=requested.memory or defaults.memory,
            timeout_seconds=requested.timeout_seconds or defaults.timeout_seconds,
            output_limit_mb=requested.output_limit_mb or defaults.output_limit_mb,
        )

    def validate(self, job: ToolJobRequest) -> None:
        if job.tool not in self.allowed_tools:
            raise PolicyViolation(f"tool type {job.tool!r} is not allowed")
        metadata = default_tool_metadata()[job.tool]
        if not re.fullmatch(self.safe_workspace_pattern, job.workspace_id):
            raise PolicyViolation("workspace_id is not safe")
        if job.workspace_id in {".", ".."} or job.workspace_id.startswith("../"):
            raise PolicyViolation("workspace_id is not safe")
        if len(job.command) > self.max_command_length:
            raise PolicyViolation("command is too long")
        if not job.command.strip():
            raise PolicyViolation("empty command is not allowed")
        if metadata.allowed_network_mode == "none" and job.network:
            raise PolicyViolation(f"network access is not allowed for {job.tool.value}")
        if metadata.allowed_network_mode == "required" and not job.network:
            raise PolicyViolation(f"network access is required for {job.tool.value}; set network=true")
        generic_default = ToolLimits()
        metadata_limits = None if job.limits.model_dump() == generic_default.model_dump() else metadata.limits
        self._validate_limits(job.limits, metadata_limits)

    def _validate_limits(self, limits: ToolLimits, defaults: ToolLimits | None = None) -> None:
        if limits.cpus <= 0 or limits.cpus > self.max_cpus:
            raise PolicyViolation("cpu limit is outside allowed range")
        if limits.timeout_seconds <= 0 or limits.timeout_seconds > self.max_timeout_seconds:
            raise PolicyViolation("timeout limit is outside allowed range")
        if limits.output_limit_mb <= 0 or limits.output_limit_mb > self.max_output_limit_mb:
            raise PolicyViolation("output limit is outside allowed range")
        if not _MEMORY_RE.fullmatch(limits.memory):
            raise PolicyViolation("memory limit has an invalid format")
        if defaults is not None:
            if limits.cpus > defaults.cpus:
                raise PolicyViolation("cpu limit exceeds tool metadata policy")
            if limits.timeout_seconds > defaults.timeout_seconds:
                raise PolicyViolation("timeout exceeds tool metadata policy")
            if limits.output_limit_mb > defaults.output_limit_mb:
                raise PolicyViolation("output limit exceeds tool metadata policy")
            if _memory_bytes(limits.memory) > _memory_bytes(defaults.memory):
                raise PolicyViolation("memory limit exceeds tool metadata policy")


DEFAULT_TOOL_POLICY = ToolPolicy()
