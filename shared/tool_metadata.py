from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from shared.schemas import ToolLimits, ToolType


@dataclass(frozen=True)
class ToolMetadata:
    name: ToolType
    version: str
    image: str
    allowed_network_mode: Literal["none", "required", "optional"]
    required_mounts: tuple[str, ...]
    timeout_seconds: int
    memory: str
    cpus: float
    output_limit_mb: int
    requires_docker: bool = True
    filesystem_policy: Literal["workspace_rw", "workspace_ro", "limited_rw"] = "workspace_rw"

    def as_dict(self) -> dict:
        return {
            "name": self.name.value,
            "version": self.version,
            "image": self.image,
            "allowed_network_mode": self.allowed_network_mode,
            "required_mounts": list(self.required_mounts),
            "timeout": self.timeout_seconds,
            "memory_limit": self.memory,
            "cpu_limit": self.cpus,
            "output_limit_mb": self.output_limit_mb,
            "requires_docker": self.requires_docker,
            "filesystem_policy": self.filesystem_policy,
        }

    @property
    def limits(self) -> ToolLimits:
        return ToolLimits(
            cpus=self.cpus,
            memory=self.memory,
            timeout_seconds=self.timeout_seconds,
            output_limit_mb=self.output_limit_mb,
        )


TOOL_IMAGE_VERSION = "0.7.0"


def default_tool_metadata(image_by_tool: dict[ToolType, str] | None = None) -> dict[ToolType, ToolMetadata]:
    images = image_by_tool or {}

    def image(tool: ToolType) -> str:
        return images.get(tool, f"local-agent/tool-{tool.value}:{TOOL_IMAGE_VERSION}")

    return {
        ToolType.shell: ToolMetadata(
            ToolType.shell,
            TOOL_IMAGE_VERSION,
            image(ToolType.shell),
            "none",
            ("/workspace",),
            300,
            "4g",
            2,
            20,
            filesystem_policy="workspace_rw",
        ),
        ToolType.test: ToolMetadata(
            ToolType.test,
            TOOL_IMAGE_VERSION,
            image(ToolType.test),
            "none",
            ("/workspace",),
            300,
            "4g",
            2,
            20,
            filesystem_policy="workspace_rw",
        ),
        ToolType.internet: ToolMetadata(
            ToolType.internet,
            TOOL_IMAGE_VERSION,
            image(ToolType.internet),
            "required",
            ("/workspace",),
            180,
            "2g",
            1,
            20,
            filesystem_policy="workspace_ro",
        ),
        ToolType.filesystem: ToolMetadata(
            ToolType.filesystem,
            TOOL_IMAGE_VERSION,
            image(ToolType.filesystem),
            "none",
            ("/workspace",),
            120,
            "1g",
            1,
            10,
            filesystem_policy="limited_rw",
        ),
        ToolType.build: ToolMetadata(
            ToolType.build,
            TOOL_IMAGE_VERSION,
            image(ToolType.build),
            "none",
            ("/workspace",),
            600,
            "8g",
            4,
            50,
            filesystem_policy="workspace_rw",
        ),
        ToolType.analysis: ToolMetadata(
            ToolType.analysis,
            TOOL_IMAGE_VERSION,
            image(ToolType.analysis),
            "none",
            ("/workspace",),
            300,
            "4g",
            2,
            20,
            filesystem_policy="workspace_ro",
        ),
    }


def metadata_for_tool(tool: ToolType, image_by_tool: dict[ToolType, str] | None = None) -> ToolMetadata:
    return default_tool_metadata(image_by_tool)[tool]
