from datetime import UTC, datetime

from sqlalchemy import create_engine, inspect

from orchestrator.app import _artifact_to_view, _tool_run_to_view
from orchestrator.db import ArtifactRecord, Base, TaskRecord, ToolRunRecord


def test_step610_task_schema_has_idempotency_and_canceled_status():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    task_columns = {column["name"] for column in inspector.get_columns("tasks")}

    assert "idempotency_key" in task_columns
    assert "uq_tasks_idempotency_key" in {constraint.name for constraint in TaskRecord.__table__.constraints}
    assert "canceled" in str(
        next(
            constraint.sqltext
            for constraint in TaskRecord.__table__.constraints
            if constraint.name == "ck_tasks_status_valid"
        )
    )


def test_step610_job_schema_has_creation_timestamps_for_sorting():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    job_columns = {column["name"] for column in inspector.get_columns("jobs")}

    assert {"created_at", "updated_at"}.issubset(job_columns)
    assert "ix_jobs_created_at" in {index["name"] for index in inspector.get_indexes("jobs")}


def test_step610_api_redacts_commands_and_paths():
    tool_run = ToolRunRecord(
        id="tool-1",
        task_id="task-1",
        job_id="job-1",
        tool_type="shell",
        command="TOKEN=secret python /tmp/private/run.py",
        status="ok",
        cpus=2,
        memory="4g",
        timeout_seconds=300,
        output_limit_mb=20,
    )
    artifact = ArtifactRecord(
        id="artifact-1",
        task_id="task-1",
        workspace_id="workspace-1",
        name="output/report.txt",
        source_path="/private/workspaces/workspace-1/output/report.txt",
        stored_path="/private/artifacts/task-1/output/report.txt",
        uri="artifact://task-1/output/report.txt",
        size_bytes=10,
        sha256="0" * 64,
        created_at=datetime.now(UTC),
    )

    assert _tool_run_to_view(tool_run).command == "[redacted]"
    assert _artifact_to_view(artifact).source_path == "[redacted]"
