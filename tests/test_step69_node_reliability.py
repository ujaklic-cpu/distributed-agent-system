from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from orchestrator.db import Base, JobRecord, NodeRecord
from orchestrator.scheduler import score_node, tool_requirements
from shared.schemas import ToolType


def test_step69_node_schema_has_reliability_columns():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    node_columns = {column["name"] for column in inspector.get_columns("nodes")}

    assert {
        "supported_tools_json",
        "docker_available",
        "platform_arch",
        "agent_version",
    }.issubset(node_columns)
    assert "ck_nodes_docker_available_bool" in {constraint.name for constraint in NodeRecord.__table__.constraints}


def test_step69_scheduler_rejects_draining_and_missing_docker_nodes():
    node = NodeRecord(
        id="node-a",
        hostname="node-a",
        base_url="http://node-a",
        status="draining",
        cpu_total=8,
        cpu_free=8,
        ram_total_gb=32,
        ram_free_gb=32,
        capabilities_json='["tool_build"]',
        supported_tools_json='["build"]',
        docker_available=1,
        platform_arch="x86_64",
        agent_version="0.7.0",
    )
    assert score_node(node, tool_requirements(ToolType.build)) == -1

    node.status = "healthy"
    node.docker_available = 0
    assert score_node(node, tool_requirements(ToolType.build)) == -1


def test_step69_scheduler_matches_supported_tool_and_capacity():
    node = NodeRecord(
        id="node-b",
        hostname="node-b",
        base_url="http://node-b",
        status="healthy",
        cpu_total=8,
        cpu_free=8,
        ram_total_gb=32,
        ram_free_gb=32,
        capabilities_json='["tool_build"]',
        supported_tools_json='["build"]',
        docker_available=1,
        platform_arch="x86_64",
        agent_version="0.7.0",
    )
    assert score_node(node, tool_requirements(ToolType.build)) > 0
    assert score_node(node, tool_requirements(ToolType.internet)) == -1


def test_step69_dead_nodes_requeue_running_jobs(monkeypatch):
    from orchestrator import registry

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr(registry, "SessionLocal", TestSession)
    monkeypatch.setattr(registry.settings, "node_unreachable_after_seconds", 5)
    monkeypatch.setattr(registry.settings, "node_dead_after_seconds", 10)

    old = datetime.now(UTC) - timedelta(seconds=30)
    with TestSession() as session:
        session.add(
            NodeRecord(
                id="node-c",
                hostname="node-c",
                base_url="http://node-c",
                status="healthy",
                cpu_total=8,
                cpu_free=8,
                ram_total_gb=32,
                ram_free_gb=32,
                running_jobs=1,
                reserved_cpu_cores=2,
                reserved_ram_gb=4,
                last_heartbeat=old,
            )
        )
        session.add(
            JobRecord(
                id="job-c",
                task_id="task-c",
                job_type="build",
                status="running",
                assigned_node_id="node-c",
                started_at=old,
                reserved_cpu_cores=2,
                reserved_ram_gb=4,
            )
        )
        session.commit()

    lost = registry.mark_stale_nodes()
    assert [job.id for job in lost] == ["job-c"]
    with TestSession() as session:
        node = session.get(NodeRecord, "node-c")
        job = session.get(JobRecord, "job-c")
        assert node.status == "dead"
        assert node.running_jobs == 0
        assert node.reserved_cpu_cores == 0
        assert job.status == "lost"
        assert job.error_code == "NODE_STALE_JOB_LOST"
