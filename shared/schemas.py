from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Generic, Literal, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


class StrictBaseModel(BaseModel):
    """Base model with safer defaults for API and job payloads."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class TaskStatus(str, Enum):
    queued = "queued"
    planning = "planning"
    delegating = "delegating"
    running_agent = "running_agent"
    running_tool = "running_tool"
    evaluating = "evaluating"
    optimizing = "optimizing"
    final_check = "final_check"
    refining = "refining"
    completed = "completed"
    done = "done"
    verified = "verified"
    unverified = "unverified"
    failed = "failed"
    requeued = "requeued"
    canceled = "canceled"


class TaskMode(str, Enum):
    strict = "strict"
    normal = "normal"
    fast = "fast"
    cheap = "cheap"


class ToolType(str, Enum):
    shell = "shell"
    test = "test"
    internet = "internet"
    filesystem = "filesystem"
    build = "build"
    analysis = "analysis"


class AgentRole(str, Enum):
    smart_agent = "smart_agent"
    code_worker = "code_worker"
    fast_worker = "fast_worker"
    research_worker = "research_worker"
    cpu_worker = "cpu_worker"


class TaskSuccessCriteria(StrictBaseModel):
    required_files: list[str] = Field(default_factory=list)
    required_commands: list[str | dict[str, Any]] = Field(default_factory=list)
    expected_output_type: Literal["text", "json", "artifact", "any"] | None = None
    verification_command: str | None = None
    acceptance_criteria: list[str | dict[str, Any]] = Field(default_factory=list)


class TaskVerificationView(StrictBaseModel):
    verifier_used: str | None = None
    status: Literal["passed", "failed", "unverified"] = "unverified"
    passed: bool | None = None
    verified: bool | None = None
    explanation: str = ""
    timestamp: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    missing: list[str] = Field(default_factory=list)
    next_action: str = ""
    problems: list[str] = Field(default_factory=list)
    score: int = 0


class TaskCreate(StrictBaseModel):
    goal: str = Field(min_length=1, max_length=20000)
    priority: int = Field(default=5, ge=0, le=10)
    workspace_id: str = Field(default_factory=lambda: new_id("workspace"))
    mode: TaskMode = TaskMode.normal
    created_by: str = "user"
    success_criteria: TaskSuccessCriteria = Field(default_factory=TaskSuccessCriteria)

    @field_validator("workspace_id")
    @classmethod
    def safe_workspace_id(cls, value: str) -> str:
        import re

        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value):
            raise ValueError("workspace_id may contain only letters, numbers, dot, underscore, and dash")
        if value in {".", ".."} or value.startswith("../"):
            raise ValueError("workspace_id is not safe")
        return value


class TaskCreated(StrictBaseModel):
    task_id: str
    status: TaskStatus
    idempotency_key: str | None = None


class TaskView(StrictBaseModel):
    task_id: str
    idempotency_key: str | None = None
    goal: str
    status: TaskStatus
    priority: int
    mode: TaskMode
    workspace_id: str
    attempts: int
    max_attempts: int
    current_phase: str | None = None
    assigned_nodes: list[str] = Field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    correlation_id: str | None = None
    duration_ms: int = 0
    created_at: datetime
    updated_at: datetime
    completed: bool = False
    verified: bool | None = None
    verification_status: str = "unverified"
    success_criteria: TaskSuccessCriteria = Field(default_factory=TaskSuccessCriteria)
    verification_details: TaskVerificationView | None = None


class TaskResult(StrictBaseModel):
    task_id: str
    idempotency_key: str | None = None
    status: TaskStatus
    result: str | None = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    completed: bool = False
    verified: bool | None = None
    verification_status: str = "unverified"
    success_criteria: TaskSuccessCriteria = Field(default_factory=TaskSuccessCriteria)
    verification_details: TaskVerificationView | None = None


class ArtifactView(StrictBaseModel):
    id: str
    task_id: str
    workspace_id: str
    name: str
    source_path: str
    uri: str
    size_bytes: int
    sha256: str
    created_at: datetime


class MemoryNamespace(str, Enum):
    global_ = "global"
    task = "task"
    user = "user"
    project = "project"
    temporary = "temporary"


class MemoryProvenance(str, Enum):
    task_result = "task_result"
    user_input = "user_input"
    operator_entry = "operator_entry"
    model_summary = "model_summary"


class MemoryCreate(StrictBaseModel):
    namespace: MemoryNamespace = Field(default=MemoryNamespace.global_)
    scope: str = Field(default="global", max_length=128)
    key: str = Field(min_length=1, max_length=255)
    value: str = Field(min_length=1, max_length=20000)
    task_id: str | None = None
    user_id: str | None = Field(default=None, max_length=128)
    project_id: str | None = Field(default=None, max_length=128)
    provenance: MemoryProvenance = MemoryProvenance.operator_entry
    provenance_ref: str | None = Field(default=None, max_length=2000)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    observed_at: datetime | None = None
    expires_at: datetime | None = None
    allow_stale_override: bool = False


class MemoryView(StrictBaseModel):
    id: str
    namespace: str
    scope: str
    key: str
    value: str
    task_id: str | None = None
    user_id: str | None = None
    project_id: str | None = None
    provenance: str = "operator_entry"
    provenance_ref: str | None = None
    confidence: float = 1.0
    observed_at: datetime
    expires_at: datetime | None = None
    deleted_at: datetime | None = None
    version: int = 1
    created_at: datetime
    updated_at: datetime


class MemoryDeleteResult(StrictBaseModel):
    id: str
    deleted: bool


class MetricsView(StrictBaseModel):
    tasks: dict[str, int] = Field(default_factory=dict)
    nodes: dict[str, int] = Field(default_factory=dict)
    jobs: dict[str, int] = Field(default_factory=dict)
    tool_runs: dict[str, int] = Field(default_factory=dict)
    optimization_rounds: int = 0
    artifacts: int = 0
    memories: int = 0
    queue_depth: int = 0
    running_jobs: int = 0
    failed_jobs: int = 0
    completed_jobs: int = 0
    node_availability: dict[str, int] = Field(default_factory=dict)
    average_job_duration_ms: float = 0
    timeout_count: int = 0
    retry_count: int = 0


class AuditLogView(StrictBaseModel):
    id: int
    event: str
    level: str
    category: str
    correlation_id: str | None = None
    task_id: str | None = None
    subtask_id: str | None = None
    job_id: str | None = None
    node_id: str | None = None
    tool_run_id: str | None = None
    artifact_id: str | None = None
    duration_ms: int = 0
    error_code: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


T = TypeVar("T")


class ApiErrorBody(StrictBaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ApiErrorResponse(StrictBaseModel):
    error: ApiErrorBody


class PageMeta(StrictBaseModel):
    limit: int
    offset: int
    total: int
    next_offset: int | None = None


class Page(StrictBaseModel, Generic[T]):
    items: list[T]
    page: PageMeta


class JobView(StrictBaseModel):
    id: str
    task_id: str
    subtask_id: str | None = None
    job_type: str
    status: str
    node_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    lost_at: datetime | None = None
    attempt_id: str | None = None
    lease_expires_at: datetime | None = None
    generation: int = 0
    error_code: str | None = None
    correlation_id: str | None = None
    duration_ms: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TaskCancelResult(StrictBaseModel):
    task_id: str
    status: TaskStatus
    canceled_jobs: int = 0


class AgentMessage(StrictBaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class AgentChatRequest(StrictBaseModel):
    role: AgentRole
    messages: list[AgentMessage]
    workspace_id: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class ToolRequestArgs(StrictBaseModel):
    command: str | None = Field(default=None, max_length=50000)
    workspace_id: str | None = Field(default=None, max_length=128)
    network: bool | None = None
    limits: ToolLimits | None = None


class ToolRequest(StrictBaseModel):
    tool: ToolType
    args: ToolRequestArgs | dict[str, Any]
    reason: str | None = None

    def normalized_args(self) -> dict[str, Any]:
        """Return tool arguments as a plain dict for execution code."""
        if isinstance(self.args, ToolRequestArgs):
            return self.args.model_dump(exclude_none=True)
        return dict(self.args)


class AgentChatResponse(StrictBaseModel):
    content: str
    tool_requests: list[ToolRequest] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)


class AgentJobRequest(StrictBaseModel):
    job_id: str = Field(default_factory=lambda: new_id("agent_job"))
    attempt_id: str | None = None
    generation: int = 0
    lease_expires_at: datetime | None = None
    task_id: str
    subtask_id: str | None = None
    role: AgentRole
    messages: list[AgentMessage]
    workspace_id: str
    options: dict[str, Any] = Field(default_factory=dict)


class AgentJobResult(StrictBaseModel):
    job_id: str
    attempt_id: str | None = None
    generation: int = 0
    node_id: str
    status: Literal["ok", "error"]
    response: AgentChatResponse | None = None
    error: str | None = None
    duration_ms: int


class ToolLimits(StrictBaseModel):
    cpus: float = Field(default=2, gt=0, le=64)
    memory: str = "4g"
    timeout_seconds: int = Field(default=300, gt=0, le=3600)
    output_limit_mb: int = Field(default=20, gt=0, le=200)


class ToolJobRequest(StrictBaseModel):
    job_id: str = Field(default_factory=lambda: new_id("tool_job"))
    attempt_id: str | None = None
    generation: int = 0
    lease_expires_at: datetime | None = None
    task_id: str
    subtask_id: str | None = None
    tool: ToolType
    workspace_id: str
    command: str = Field(min_length=1, max_length=50000)
    network: bool = False
    limits: ToolLimits = Field(default_factory=ToolLimits)


class ToolMetadataView(StrictBaseModel):
    name: str
    version: str
    image: str
    allowed_network_mode: str
    required_mounts: list[str] = Field(default_factory=list)
    timeout: int
    memory_limit: str
    cpu_limit: float
    output_limit_mb: int
    requires_docker: bool = True
    filesystem_policy: str = "workspace_rw"


class ToolContainerError(StrictBaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ToolContainerArtifact(StrictBaseModel):
    name: str = Field(min_length=1, max_length=512)
    path: str = Field(min_length=1, max_length=2048)
    size_bytes: int = Field(default=0, ge=0)

    @field_validator("path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        import posixpath

        normalized = posixpath.normpath(value)
        if value.startswith("/") or normalized.startswith("../") or normalized in {".", ".."}:
            raise ValueError("artifact path must be relative to the workspace")
        return value


class ToolContainerResult(StrictBaseModel):
    schema_version: Literal["tool-result.v1"] = "tool-result.v1"
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = Field(default=0, ge=0)
    artifacts: list[ToolContainerArtifact] = Field(default_factory=list)
    error: ToolContainerError | None = None
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    result_truncated: bool = False


class ToolJobResult(StrictBaseModel):
    job_id: str
    attempt_id: str | None = None
    generation: int = 0
    node_id: str
    status: Literal["ok", "error", "timeout", "blocked", "canceled"]
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    artifacts: list[ToolContainerArtifact] = Field(default_factory=list)
    duration_ms: int
    error: str | None = None


class ToolRunView(StrictBaseModel):
    id: str
    task_id: str
    subtask_id: str | None = None
    node_id: str | None = None
    tool_type: str
    command: str
    network: bool = False
    limits: dict[str, Any] = Field(default_factory=dict)
    status: str
    exit_code: int | None = None
    stdout_excerpt: str = ""
    stderr_excerpt: str = ""
    error: str | None = None
    error_code: str | None = None
    correlation_id: str | None = None
    duration_ms: int = 0


class NodeGpu(StrictBaseModel):
    id: str
    name: str | None = None
    vram_gb_total: float = 0
    vram_gb_free: float = 0


class NodeRegister(StrictBaseModel):
    node_id: str
    hostname: str
    cpu_cores_total: int
    ram_gb_total: float
    gpus: list[NodeGpu] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    supported_tools: list[ToolType] = Field(default_factory=list)
    docker_available: bool = False
    platform_arch: str = "unknown"
    agent_version: str = "0.0.0"
    protocol_version: str = "2026-06-14"
    base_url: str | None = None


class NodeHeartbeat(StrictBaseModel):
    status: Literal["healthy", "unreachable", "dead", "recovered", "draining", "incompatible"] = "healthy"
    cpu_cores_free: int
    ram_gb_free: float
    gpus: list[NodeGpu] = Field(default_factory=list)
    running_jobs: int = 0
    loaded_models: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    supported_tools: list[ToolType] = Field(default_factory=list)
    docker_available: bool = False
    platform_arch: str = "unknown"
    agent_version: str = "0.0.0"
    protocol_version: str = "2026-06-14"


class NodeView(StrictBaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid", validate_assignment=True)

    node_id: str
    hostname: str
    base_url: str
    status: str
    cpu_cores_total: int
    cpu_cores_free: int
    ram_gb_total: float
    ram_gb_free: float
    gpus: list[dict[str, Any]] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    supported_tools: list[str] = Field(default_factory=list)
    docker_available: bool = False
    platform_arch: str = "unknown"
    agent_version: str = "0.0.0"
    protocol_version: str = "2026-06-14"
    loaded_models: list[str] = Field(default_factory=list)
    running_jobs: int = 0
    reserved_cpu_cores: int = 0
    reserved_ram_gb: float = 0
    reserved_vram: dict[str, float] = Field(default_factory=dict)
    version: int = 0
    last_heartbeat: datetime | None = None
