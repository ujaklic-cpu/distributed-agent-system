from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from orchestrator.config import settings
from shared.statuses import JOB_STATUSES, OPTIMIZATION_STATUSES, SUBTASK_STATUSES, TASK_STATUSES, TOOL_RUN_STATUSES

NODE_STATUSES = ("healthy", "unreachable", "dead", "recovered", "draining", "incompatible")
TASK_MODES = ("strict", "normal", "fast", "cheap")
MEMORY_NAMESPACES = ("global", "task", "user", "project", "temporary")
MEMORY_PROVENANCE = ("task_result", "user_input", "operator_entry", "model_summary")


def _status_check(column_name: str, values: tuple[str, ...], constraint_name: str) -> CheckConstraint:
    allowed = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{column_name} IN ({allowed})", name=constraint_name)


class Base(DeclarativeBase):
    pass


class TaskRecord(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        _status_check("status", TASK_STATUSES, "ck_tasks_status_valid"),
        _status_check("mode", TASK_MODES, "ck_tasks_mode_valid"),
        CheckConstraint("priority >= 0 AND priority <= 10", name="ck_tasks_priority_range"),
        CheckConstraint("attempts >= 0", name="ck_tasks_attempts_nonnegative"),
        CheckConstraint("max_attempts >= 1", name="ck_tasks_max_attempts_positive"),
        Index("ix_tasks_status", "status"),
        Index("ix_tasks_created_at", "created_at"),
        Index("ix_tasks_idempotency_key", "idempotency_key"),
        CheckConstraint("duration_ms >= 0", name="ck_tasks_duration_nonnegative"),
        UniqueConstraint("idempotency_key", name="uq_tasks_idempotency_key"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="normal")
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="user")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    current_phase: Mapped[str | None] = mapped_column(String(128), nullable=True)
    assigned_nodes: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifacts_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_criteria_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    verification_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SubtaskRecord(Base):
    __tablename__ = "subtasks"
    __table_args__ = (
        _status_check("status", SUBTASK_STATUSES, "ck_subtasks_status_valid"),
        CheckConstraint("difficulty >= 0 AND difficulty <= 10", name="ck_subtasks_difficulty_range"),
        CheckConstraint("risk >= 0 AND risk <= 10", name="ck_subtasks_risk_range"),
        Index("ix_subtasks_task_id", "task_id"),
        CheckConstraint("duration_ms >= 0", name="ck_subtasks_duration_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    executor_type: Mapped[str] = mapped_column(String(64), nullable=False)
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    risk: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    success_condition: Mapped[str] = mapped_column(Text, nullable=False, default="")
    assigned_node_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True
    )
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class NodeRecord(Base):
    __tablename__ = "nodes"
    __table_args__ = (
        _status_check("status", NODE_STATUSES, "ck_nodes_status_valid"),
        CheckConstraint("cpu_total >= 0", name="ck_nodes_cpu_total_nonnegative"),
        CheckConstraint("cpu_free >= 0", name="ck_nodes_cpu_free_nonnegative"),
        CheckConstraint("ram_total_gb >= 0", name="ck_nodes_ram_total_nonnegative"),
        CheckConstraint("ram_free_gb >= 0", name="ck_nodes_ram_free_nonnegative"),
        CheckConstraint("running_jobs >= 0", name="ck_nodes_running_jobs_nonnegative"),
        CheckConstraint("docker_available IN (0, 1)", name="ck_nodes_docker_available_bool"),
        CheckConstraint("reserved_cpu_cores >= 0", name="ck_nodes_reserved_cpu_nonnegative"),
        CheckConstraint("reserved_ram_gb >= 0", name="ck_nodes_reserved_ram_nonnegative"),
        Index("ix_nodes_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    base_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="healthy")
    cpu_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cpu_free: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ram_total_gb: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    ram_free_gb: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    gpu_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    capabilities_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    supported_tools_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    docker_available: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    platform_arch: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    agent_version: Mapped[str] = mapped_column(String(64), nullable=False, default="0.0.0")
    protocol_version: Mapped[str] = mapped_column(String(64), nullable=False, default="2026-06-14")
    loaded_models_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    running_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved_cpu_cores: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved_ram_gb: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    reserved_vram_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    @property
    def gpus(self) -> list[dict]:
        import json

        try:
            value = json.loads(self.gpu_json or "[]")
            return value if isinstance(value, list) else []
        except json.JSONDecodeError:
            return []

    @property
    def capabilities(self) -> list[str]:
        import json

        try:
            value = json.loads(self.capabilities_json or "[]")
            return value if isinstance(value, list) else []
        except json.JSONDecodeError:
            return []

    @property
    def supported_tools(self) -> list[str]:
        import json

        try:
            value = json.loads(self.supported_tools_json or "[]")
            return value if isinstance(value, list) else []
        except json.JSONDecodeError:
            return []

    @property
    def loaded_models(self) -> list[str]:
        import json

        try:
            value = json.loads(self.loaded_models_json or "[]")
            return value if isinstance(value, list) else []
        except json.JSONDecodeError:
            return []

    @property
    def reserved_vram(self) -> dict[str, float]:
        import json

        try:
            value = json.loads(self.reserved_vram_json or "{}")
            if not isinstance(value, dict):
                return {}
            return {str(key): float(amount) for key, amount in value.items()}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}


class JobRecord(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        _status_check("status", JOB_STATUSES, "ck_jobs_status_valid"),
        CheckConstraint("generation >= 0", name="ck_jobs_generation_nonnegative"),
        CheckConstraint("duration_ms >= 0", name="ck_jobs_duration_nonnegative"),
        CheckConstraint("reserved_cpu_cores >= 0", name="ck_jobs_reserved_cpu_nonnegative"),
        CheckConstraint("reserved_ram_gb >= 0", name="ck_jobs_reserved_ram_nonnegative"),
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_task_id", "task_id"),
        Index("ix_jobs_node_id", "node_id"),
        Index("ix_jobs_subtask_id", "subtask_id"),
        Index("ix_jobs_job_type", "job_type"),
        Index("ix_jobs_attempt_id", "attempt_id"),
        Index("ix_jobs_lease_expires_at", "lease_expires_at"),
        Index("ix_jobs_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    subtask_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("subtasks.id", ondelete="SET NULL"), nullable=True
    )
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    assigned_node_id: Mapped[str | None] = mapped_column(
        "node_id", String(128), ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lost_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    reserved_cpu_cores: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved_ram_gb: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    reserved_vram_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WorkspaceLockRecord(Base):
    __tablename__ = "workspace_locks"
    __table_args__ = (Index("ix_workspace_locks_expires_at", "expires_at"),)

    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    lock_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64), ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ToolRunRecord(Base):
    __tablename__ = "tool_runs"
    __table_args__ = (
        _status_check("status", TOOL_RUN_STATUSES, "ck_tool_runs_status_valid"),
        CheckConstraint("network IN (0, 1)", name="ck_tool_runs_network_bool"),
        CheckConstraint("cpus > 0", name="ck_tool_runs_cpus_positive"),
        CheckConstraint("timeout_seconds > 0", name="ck_tool_runs_timeout_positive"),
        CheckConstraint("output_limit_mb > 0", name="ck_tool_runs_output_limit_positive"),
        CheckConstraint("duration_ms >= 0", name="ck_tool_runs_duration_nonnegative"),
        Index("ix_tool_runs_task_id", "task_id"),
        Index("ix_tool_runs_job_id", "job_id"),
        Index("ix_tool_runs_node_id", "node_id"),
        Index("ix_tool_runs_subtask_id", "subtask_id"),
        Index("ix_tool_runs_tool_type", "tool_type"),
        Index("ix_tool_runs_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    job_id: Mapped[str] = mapped_column(String(64), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    subtask_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("subtasks.id", ondelete="SET NULL"), nullable=True
    )
    node_id: Mapped[str | None] = mapped_column(String(128), ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True)
    tool_type: Mapped[str] = mapped_column(String(64), nullable=False)
    command: Mapped[str] = mapped_column(Text, nullable=False)
    network: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cpus: Mapped[float] = mapped_column(Float, nullable=False, default=2)
    memory: Mapped[str] = mapped_column(String(32), nullable=False, default="4g")
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    output_limit_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stdout_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    stderr_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    stdout_excerpt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stderr_excerpt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class OptimizationRoundRecord(Base):
    __tablename__ = "optimization_rounds"
    __table_args__ = (
        _status_check("status", OPTIMIZATION_STATUSES, "ck_optimization_rounds_status_valid"),
        CheckConstraint("round_index >= 0", name="ck_optimization_rounds_index_nonnegative"),
        Index("ix_optimization_rounds_task_id", "task_id"),
        Index("ix_optimization_rounds_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    round_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    score_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ArtifactRecord(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="ck_artifacts_size_nonnegative"),
        Index("ix_artifacts_task_id", "task_id"),
        Index("ix_artifacts_workspace_id", "workspace_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MemoryRecord(Base):
    __tablename__ = "memories"
    __table_args__ = (
        UniqueConstraint("namespace", "scope", "key"),
        _status_check("namespace", MEMORY_NAMESPACES, "ck_memories_namespace_valid"),
        _status_check("provenance", MEMORY_PROVENANCE, "ck_memories_provenance_valid"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_memories_confidence_range"),
        Index("ix_memories_namespace", "namespace"),
        Index("ix_memories_scope", "scope"),
        Index("ix_memories_task_id", "task_id"),
        Index("ix_memories_expires_at", "expires_at"),
        Index("ix_memories_observed_at", "observed_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(32), nullable=False, default="global")
    scope: Mapped[str] = mapped_column(String(128), nullable=False, default="global")
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provenance: Mapped[str] = mapped_column(String(32), nullable=False, default="operator_entry")
    provenance_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AuditLogRecord(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_task_id", "task_id"),
        Index("ix_audit_logs_created_at", "created_at"),
        Index("ix_audit_logs_event", "event"),
        Index("ix_audit_logs_correlation_id", "correlation_id"),
        Index("ix_audit_logs_category", "category"),
        CheckConstraint("duration_ms >= 0", name="ck_audit_logs_duration_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event: Mapped[str] = mapped_column(String(128), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="scheduler")
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    subtask_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    node_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    artifact_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    """Validate that migrations have created the database schema.

    Application startup no longer patches tables at runtime. Run
    `alembic upgrade head` before starting the orchestrator or worker.
    Tests may still call `Base.metadata.create_all()` on isolated SQLite
    databases.
    """
    from sqlalchemy import inspect

    inspector = inspect(engine)
    required_tables = {"tasks", "jobs", "nodes", "audit_logs", "tool_runs"}
    existing_tables = set(inspector.get_table_names())
    missing = required_tables - existing_tables
    if missing:
        raise RuntimeError(
            "database schema is not initialized; run `alembic upgrade head` first; "
            f"missing tables: {', '.join(sorted(missing))}"
        )


def run_retention_policy(now: datetime | None = None, *, dry_run: bool = False) -> dict[str, int]:
    """Delete aged operational records according to configured retention windows.

    Retention intentionally removes dependent rows before parent task rows. A
    value of 0 disables a window.
    """
    now = now or datetime.now(UTC)
    deleted = {"audit_logs": 0, "tool_runs": 0, "tasks": 0, "memories": 0}
    with SessionLocal() as session:
        if settings.audit_log_retention_days > 0:
            cutoff = now - timedelta(days=settings.audit_log_retention_days)
            audit_query = session.query(AuditLogRecord).filter(AuditLogRecord.created_at < cutoff)
            if dry_run:
                deleted["audit_logs"] = int(audit_query.count())
            else:
                result = session.execute(delete(AuditLogRecord).where(AuditLogRecord.created_at < cutoff))
                deleted["audit_logs"] = int(result.rowcount or 0)
        if settings.tool_run_retention_days > 0:
            cutoff = now - timedelta(days=settings.tool_run_retention_days)
            tool_run_query = session.query(ToolRunRecord).filter(
                ToolRunRecord.finished_at.is_not(None), ToolRunRecord.finished_at < cutoff
            )
            if dry_run:
                deleted["tool_runs"] = int(tool_run_query.count())
            else:
                result = session.execute(
                    delete(ToolRunRecord).where(
                        ToolRunRecord.finished_at.is_not(None), ToolRunRecord.finished_at < cutoff
                    )
                )
                deleted["tool_runs"] = int(result.rowcount or 0)
        if settings.task_retention_days > 0:
            cutoff = now - timedelta(days=settings.task_retention_days)
            terminal_statuses = ("completed", "done", "verified", "unverified", "failed", "canceled")
            task_query = session.query(TaskRecord).filter(
                TaskRecord.status.in_(terminal_statuses),
                TaskRecord.updated_at < cutoff,
            )
            if dry_run:
                deleted["tasks"] = int(task_query.count())
            else:
                result = session.execute(
                    delete(TaskRecord).where(
                        TaskRecord.status.in_(terminal_statuses),
                        TaskRecord.updated_at < cutoff,
                    )
                )
                deleted["tasks"] = int(result.rowcount or 0)
        memory_filters = []
        if settings.memory_retention_days > 0:
            memory_filters.append(MemoryRecord.updated_at < now - timedelta(days=settings.memory_retention_days))
        if settings.temporary_memory_retention_hours > 0:
            memory_filters.append(
                (MemoryRecord.namespace == "temporary")
                & (MemoryRecord.updated_at < now - timedelta(hours=settings.temporary_memory_retention_hours))
            )
        memory_filters.append(MemoryRecord.expires_at.is_not(None) & (MemoryRecord.expires_at < now))
        memory_filters.append(MemoryRecord.deleted_at.is_not(None))
        if memory_filters:
            from sqlalchemy import or_

            memory_query = session.query(MemoryRecord).filter(or_(*memory_filters))
            if dry_run:
                deleted["memories"] = int(memory_query.count())
            else:
                result = session.execute(delete(MemoryRecord).where(or_(*memory_filters)))
                deleted["memories"] = int(result.rowcount or 0)
        if not dry_run:
            session.commit()
    return deleted
