from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, inspect

from orchestrator.app import _audit_memory_access, _memory_to_view
from orchestrator.db import AuditLogRecord, Base, MemoryRecord
from shared.schemas import MemoryCreate, MemoryNamespace, MemoryProvenance


def test_step613_memory_schema_has_namespaces_provenance_confidence_and_retention_fields():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("memories")}

    assert {
        "namespace",
        "provenance",
        "provenance_ref",
        "confidence",
        "observed_at",
        "expires_at",
        "deleted_at",
        "version",
    }.issubset(columns)
    constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in MemoryRecord.__table__.constraints
        if getattr(constraint, "name", None)
    }
    assert "ck_memories_namespace_valid" in constraints
    assert "global" in constraints["ck_memories_namespace_valid"]
    assert "temporary" in constraints["ck_memories_namespace_valid"]
    assert "ck_memories_provenance_valid" in constraints
    assert "task_result" in constraints["ck_memories_provenance_valid"]
    assert "ck_memories_confidence_range" in constraints


def test_step613_memory_create_schema_supports_namespaces_and_provenance():
    payload = MemoryCreate(
        namespace=MemoryNamespace.task,
        scope="task-1",
        key="decision",
        value="Use the verified artifact",
        task_id="task-1",
        provenance=MemoryProvenance.model_summary,
        confidence=0.75,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )

    assert payload.namespace == MemoryNamespace.task
    assert payload.provenance == MemoryProvenance.model_summary
    assert payload.confidence == 0.75


def test_step613_memory_view_exposes_metadata():
    now = datetime.now(UTC)
    row = MemoryRecord(
        id="memory-1",
        namespace="project",
        scope="project-alpha",
        key="api_policy",
        value="Redact command strings",
        task_id="task-1",
        project_id="project-alpha",
        provenance="operator_entry",
        provenance_ref="runbook-1",
        confidence=0.9,
        observed_at=now,
        expires_at=now + timedelta(days=30),
        version=3,
        created_at=now,
        updated_at=now,
    )

    view = _memory_to_view(row)

    assert view.namespace == "project"
    assert view.project_id == "project-alpha"
    assert view.provenance_ref == "runbook-1"
    assert view.confidence == 0.9
    assert view.version == 3


def test_step613_memory_read_audit_uses_memory_category():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=engine)
    with Session() as session:
        _audit_memory_access(session, "memory_read", namespace="global", scope="global", key="k", result_count=1)
        session.commit()
        row = session.query(AuditLogRecord).one()

    assert row.event == "memory_read"
    assert row.category == "memory"
    assert row.level == "info"
    assert '"result_count": 1' in row.payload
