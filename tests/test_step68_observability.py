from sqlalchemy import create_engine, inspect

from orchestrator.db import AuditLogRecord, Base, JobRecord, SubtaskRecord, TaskRecord, ToolRunRecord
from shared.logging import infer_category, normalize_level
from shared.schemas import MetricsView


def test_step68_schema_has_observability_columns():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)

    task_columns = {column["name"] for column in inspector.get_columns("tasks")}
    subtask_columns = {column["name"] for column in inspector.get_columns("subtasks")}
    job_columns = {column["name"] for column in inspector.get_columns("jobs")}
    tool_columns = {column["name"] for column in inspector.get_columns("tool_runs")}
    audit_columns = {column["name"] for column in inspector.get_columns("audit_logs")}
    audit_indexes = {index["name"] for index in inspector.get_indexes("audit_logs")}

    assert {"correlation_id", "error_code", "duration_ms"}.issubset(task_columns)
    assert {"correlation_id", "error_code", "duration_ms", "started_at", "finished_at"}.issubset(subtask_columns)
    assert {"correlation_id", "error_code", "duration_ms"}.issubset(job_columns)
    assert {"correlation_id", "error_code", "duration_ms"}.issubset(tool_columns)
    assert {
        "level",
        "category",
        "correlation_id",
        "subtask_id",
        "job_id",
        "node_id",
        "tool_run_id",
        "artifact_id",
        "duration_ms",
        "error_code",
    }.issubset(audit_columns)
    assert {"ix_audit_logs_correlation_id", "ix_audit_logs_category"}.issubset(audit_indexes)


def test_step68_constraints_include_nonnegative_durations():
    assert "ck_tasks_duration_nonnegative" in {constraint.name for constraint in TaskRecord.__table__.constraints}
    assert "ck_subtasks_duration_nonnegative" in {constraint.name for constraint in SubtaskRecord.__table__.constraints}
    assert "ck_jobs_duration_nonnegative" in {constraint.name for constraint in JobRecord.__table__.constraints}
    assert "ck_tool_runs_duration_nonnegative" in {
        constraint.name for constraint in ToolRunRecord.__table__.constraints
    }
    assert "ck_audit_logs_duration_nonnegative" in {
        constraint.name for constraint in AuditLogRecord.__table__.constraints
    }


def test_step68_logging_taxonomy_and_metrics_fields():
    assert normalize_level("WARNING") == "warning"
    assert normalize_level("unexpected") == "info"
    assert infer_category("tool_finished") == "tool"
    assert infer_category("final_check_finished") == "verification"

    metrics = MetricsView()
    assert metrics.queue_depth == 0
    assert metrics.running_jobs == 0
    assert metrics.failed_jobs == 0
    assert metrics.completed_jobs == 0
    assert metrics.node_availability == {}
    assert metrics.average_job_duration_ms == 0
    assert metrics.timeout_count == 0
    assert metrics.retry_count == 0
