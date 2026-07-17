from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from shared.schemas import AgentRole, TaskMode, ToolType


def _reserved_vram(node: Any) -> dict[str, float]:
    value = getattr(node, "reserved_vram", None)
    if isinstance(value, dict):
        return {str(key): float(amount) for key, amount in value.items()}
    raw = getattr(node, "reserved_vram_json", "{}")
    if not raw:
        return {}
    try:
        import json

        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return {}
        return {str(key): float(amount) for key, amount in parsed.items()}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def available_cpu_cores(node: Any) -> int:
    return max(0, int(getattr(node, "cpu_free", 0)) - int(getattr(node, "reserved_cpu_cores", 0) or 0))


def available_ram_gb(node: Any) -> float:
    return max(0.0, float(getattr(node, "ram_free_gb", 0)) - float(getattr(node, "reserved_ram_gb", 0) or 0))


def available_gpu_vram(node: Any, gpu: dict[str, Any]) -> float:
    gpu_id = str(gpu.get("id") or "")
    reserved = _reserved_vram(node).get(gpu_id, 0.0)
    return max(0.0, float(gpu.get("vram_gb_free", 0) or 0) - reserved)


@dataclass(frozen=True)
class JobRequirements:
    job_type: str
    requires_gpu: bool = False
    preferred_gpu: bool = False
    min_vram_gb: float = 0
    min_ram_gb: float = 1
    min_cpu_cores: int = 1
    preferred_model: str | None = None
    required_capability: str | None = None
    network: bool = False
    model_class: str | None = None
    acceptable_models: tuple[str, ...] = ()
    required_tool: ToolType | None = None
    requires_docker: bool = False
    platform_arch: str | None = None


MODEL_CLASSES: dict[str, dict[str, Any]] = {
    "smart": {
        "preferred": ("qwen2.5-coder:14b",),
        "min_vram_gb": 6,
        "min_ram_gb": 8,
    },
    "hard_code": {
        "preferred": ("qwen2.5-coder:14b",),
        "min_vram_gb": 6,
        "min_ram_gb": 8,
    },
    "small_code": {
        "preferred": ("gemma2:2b",),
        "min_vram_gb": 0,
        "min_ram_gb": 2,
    },
    "fast": {
        "preferred": ("gemma2:2b",),
        "min_vram_gb": 0,
        "min_ram_gb": 2,
    },
}


ROLE_REQUIREMENTS: dict[AgentRole, JobRequirements] = {
    AgentRole.smart_agent: JobRequirements(
        "smart_reasoning",
        False,
        True,
        MODEL_CLASSES["smart"]["min_vram_gb"],
        MODEL_CLASSES["smart"]["min_ram_gb"],
        2,
        "qwen2.5-coder:14b",
        "smart_agent",
        model_class="smart",
        acceptable_models=MODEL_CLASSES["smart"]["preferred"],
    ),
    AgentRole.code_worker: JobRequirements(
        "code_worker",
        False,
        False,
        0,
        2,
        1,
        "gemma2:2b",
        "code_worker",
        model_class="small_code",
        acceptable_models=MODEL_CLASSES["small_code"]["preferred"],
    ),
    AgentRole.fast_worker: JobRequirements(
        "fast_worker",
        False,
        False,
        0,
        2,
        1,
        "gemma2:2b",
        "fast_worker",
        model_class="fast",
        acceptable_models=MODEL_CLASSES["fast"]["preferred"],
    ),
    AgentRole.research_worker: JobRequirements(
        "research_worker",
        False,
        False,
        0,
        4,
        1,
        "llama3.1:8b",
        "research_worker",
        False,
        model_class="fast",
        acceptable_models=MODEL_CLASSES["fast"]["preferred"],
    ),
    AgentRole.cpu_worker: JobRequirements("cpu_worker", False, False, 0, 2, 1, None, "cpu_worker", model_class="fast"),
}


TOOL_REQUIREMENTS: dict[ToolType, JobRequirements] = {
    ToolType.shell: JobRequirements(
        "shell_tool", False, False, 0, 2, 1, None, "tool_shell", required_tool=ToolType.shell, requires_docker=True
    ),
    ToolType.test: JobRequirements(
        "test_runner", False, False, 0, 4, 2, None, "tool_test", required_tool=ToolType.test, requires_docker=True
    ),
    ToolType.internet: JobRequirements(
        "internet_tool",
        False,
        False,
        0,
        2,
        1,
        None,
        "tool_internet",
        True,
        required_tool=ToolType.internet,
        requires_docker=True,
    ),
    ToolType.filesystem: JobRequirements(
        "filesystem_tool",
        False,
        False,
        0,
        2,
        1,
        None,
        "tool_filesystem",
        required_tool=ToolType.filesystem,
        requires_docker=True,
    ),
    ToolType.build: JobRequirements(
        "build_tool", False, False, 0, 4, 2, None, "tool_build", required_tool=ToolType.build, requires_docker=True
    ),
    ToolType.analysis: JobRequirements(
        "analysis_tool",
        False,
        False,
        0,
        2,
        1,
        None,
        "tool_analysis",
        required_tool=ToolType.analysis,
        requires_docker=True,
    ),
}


def apply_mode_downgrade(requirements: JobRequirements, mode: TaskMode = TaskMode.normal) -> JobRequirements:
    """Relax model requirements for fast and cheap task modes."""
    if mode == TaskMode.strict:
        return requirements
    if requirements.model_class == "smart":
        if mode == TaskMode.fast:
            return replace(requirements, min_vram_gb=0, min_ram_gb=2, preferred_model=None)
        if mode == TaskMode.cheap:
            return replace(requirements, min_vram_gb=0, min_ram_gb=2, requires_gpu=False, preferred_model=None)
        return requirements
    if mode == TaskMode.fast:
        return replace(
            requirements, min_ram_gb=max(2, requirements.min_ram_gb / 2), min_vram_gb=0, preferred_model=None
        )
    if mode == TaskMode.cheap:
        return replace(
            requirements,
            min_ram_gb=max(1, requirements.min_ram_gb / 4),
            min_vram_gb=0,
            preferred_gpu=False,
            preferred_model=None,
        )
    return requirements


def role_requirements(role: AgentRole, mode: TaskMode = TaskMode.normal) -> JobRequirements:
    return apply_mode_downgrade(ROLE_REQUIREMENTS.get(role, ROLE_REQUIREMENTS[AgentRole.fast_worker]), mode)


def tool_requirements(tool: ToolType, network: bool = False) -> JobRequirements:
    req = TOOL_REQUIREMENTS.get(tool, TOOL_REQUIREMENTS[ToolType.shell])
    if network and tool != ToolType.internet:
        return replace(req, network=True)
    return req


def score_node(node: Any, job: JobRequirements) -> float:
    if node.status != "healthy":
        return -1
    if job.requires_docker and not bool(getattr(node, "docker_available", 0)):
        return -1
    if job.platform_arch and str(getattr(node, "platform_arch", "")) != job.platform_arch:
        return -1
    effective_ram = available_ram_gb(node)
    effective_cpu = available_cpu_cores(node)
    if effective_ram < job.min_ram_gb:
        return -1
    if effective_cpu < job.min_cpu_cores:
        return -1

    gpus = node.gpus or []
    best_gpu_free = max((available_gpu_vram(node, gpu) for gpu in gpus), default=0.0)
    if job.requires_gpu and not gpus:
        return -1
    if job.min_vram_gb and best_gpu_free < job.min_vram_gb:
        return -1

    capabilities = set(node.capabilities or [])
    if job.required_capability and job.required_capability not in capabilities:
        return -1
    if job.network and "tool_internet" not in capabilities and job.required_capability != "tool_internet":
        return -1
    supported_tools = set(getattr(node, "supported_tools", []) or [])
    if job.required_tool and job.required_tool.value not in supported_tools:
        return -1

    score = 0.0
    score += effective_cpu * 2
    score += effective_ram
    score += best_gpu_free * 4
    score -= int(getattr(node, "running_jobs", 0) or 0) * 10
    if job.preferred_gpu and best_gpu_free > 0:
        score += 20

    loaded_models = set(node.loaded_models or [])
    if job.preferred_model and job.preferred_model in loaded_models:
        score += 100
    elif job.acceptable_models and loaded_models.intersection(job.acceptable_models):
        score += 75
    if job.required_capability and job.required_capability in capabilities:
        score += 50
    if job.required_tool and job.required_tool.value in set(getattr(node, "supported_tools", []) or []):
        score += 25
    if job.requires_docker and bool(getattr(node, "docker_available", 0)):
        score += 10
    return score
