from sqlalchemy import create_engine, inspect

from orchestrator.db import Base, JobRecord, TaskRecord, ToolRunRecord


def test_step67_schema_has_requested_indexes_and_columns():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)

    task_indexes = {index["name"] for index in inspector.get_indexes("tasks")}
    job_indexes = {index["name"] for index in inspector.get_indexes("jobs")}
    audit_indexes = {index["name"] for index in inspector.get_indexes("audit_logs")}
    tool_indexes = {index["name"] for index in inspector.get_indexes("tool_runs")}
    tool_columns = {column["name"] for column in inspector.get_columns("tool_runs")}
    job_columns = {column["name"] for column in inspector.get_columns("jobs")}

    assert {"ix_tasks_status", "ix_tasks_created_at"}.issubset(task_indexes)
    assert {"ix_jobs_status", "ix_jobs_task_id", "ix_jobs_node_id"}.issubset(job_indexes)
    assert {"ix_audit_logs_task_id", "ix_audit_logs_created_at"}.issubset(audit_indexes)
    assert {"ix_tool_runs_task_id", "ix_tool_runs_job_id"}.issubset(tool_indexes)
    assert "job_id" in tool_columns
    assert "node_id" in job_columns


def test_step67_schema_defines_integrity_constraints():
    task_constraints = {constraint.name for constraint in TaskRecord.__table__.constraints}
    job_constraints = {constraint.name for constraint in JobRecord.__table__.constraints}
    tool_constraints = {constraint.name for constraint in ToolRunRecord.__table__.constraints}

    assert "ck_tasks_status_valid" in task_constraints
    assert "ck_jobs_status_valid" in job_constraints
    assert "ck_tool_runs_status_valid" in tool_constraints
    assert "ck_tool_runs_network_bool" in tool_constraints
