from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from orchestrator import registry
from orchestrator.db import Base, JobRecord, NodeRecord, WorkspaceLockRecord
from orchestrator.scheduler import JobRequirements


def _session(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'step53.db'}")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr(registry, "SessionLocal", Session)
    return Session


def _add_node(Session, node_id="node-1", heartbeat=None):
    with Session() as session:
        session.add(
            NodeRecord(
                id=node_id,
                hostname=node_id,
                base_url=f"http://{node_id}",
                status="healthy",
                cpu_total=8,
                cpu_free=8,
                ram_total_gb=16,
                ram_free_gb=16,
                gpu_json="[]",
                capabilities_json=json.dumps(["cpu_worker", "tool_shell"]),
                loaded_models_json="[]",
                last_heartbeat=heartbeat or datetime.now(UTC),
            )
        )
        session.commit()


def test_stale_result_is_rejected_and_current_attempt_is_accepted(tmp_path, monkeypatch):
    Session = _session(tmp_path, monkeypatch)
    _add_node(Session)
    job_id, node = registry.create_reserved_job(
        "task-1", JobRequirements("cpu_worker", min_cpu_cores=1, min_ram_gb=1, required_capability="cpu_worker")
    )
    ctx = registry.get_job_execution_context(job_id)

    accepted, reason = registry.accept_job_result(job_id, "wrong", node.id, ctx["generation"], {"status": "ok"})
    assert not accepted
    assert reason == "stale attempt"

    accepted, reason = registry.accept_job_result(
        job_id, ctx["attempt_id"], node.id, ctx["generation"], {"status": "ok"}
    )
    assert accepted
    assert reason == "accepted"


def test_lease_expiry_marks_job_lost_and_releases_reservation(tmp_path, monkeypatch):
    Session = _session(tmp_path, monkeypatch)
    _add_node(Session)
    job_id, _node = registry.create_reserved_job(
        "task-1", JobRequirements("cpu_worker", min_cpu_cores=2, min_ram_gb=2, required_capability="cpu_worker")
    )
    with Session() as session:
        job = session.get(JobRecord, job_id)
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    lost = registry.cleanup_expired_job_leases()
    assert [job.id for job in lost] == [job_id]
    with Session() as session:
        job = session.get(JobRecord, job_id)
        node = session.get(NodeRecord, "node-1")
        assert job.status == "lost"
        assert node.reserved_cpu_cores == 0
        assert node.reserved_ram_gb == 0


def test_workspace_write_lock_blocks_other_locks_until_release(tmp_path, monkeypatch):
    _session(tmp_path, monkeypatch)
    assert registry.acquire_workspace_lock("ws", "write", "job-1", ttl_seconds=60)
    assert not registry.acquire_workspace_lock("ws", "read", "job-2", ttl_seconds=60)
    assert not registry.acquire_workspace_lock("ws", "write", "job-2", ttl_seconds=60)
    registry.release_workspace_lock("job-1", "ws")
    assert registry.acquire_workspace_lock("ws", "read", "job-2", ttl_seconds=60)
    assert registry.acquire_workspace_lock("ws", "read", "job-3", ttl_seconds=60)


def test_node_death_marks_running_jobs_lost_and_clears_locks(tmp_path, monkeypatch):
    Session = _session(tmp_path, monkeypatch)
    _add_node(Session)
    monkeypatch.setattr(registry.settings, "node_unreachable_after_seconds", 1)
    monkeypatch.setattr(registry.settings, "node_dead_after_seconds", 2)
    job_id, _node = registry.create_reserved_job(
        "task-1", JobRequirements("cpu_worker", min_cpu_cores=1, min_ram_gb=1, required_capability="cpu_worker")
    )
    assert registry.acquire_workspace_lock("ws", "write", job_id)
    with Session() as session:
        node = session.get(NodeRecord, "node-1")
        node.last_heartbeat = datetime.now(UTC) - timedelta(seconds=999)
        session.commit()

    lost = registry.mark_stale_nodes()
    assert [job.id for job in lost] == [job_id]
    with Session() as session:
        assert session.get(JobRecord, job_id).status == "lost"
        assert session.query(WorkspaceLockRecord).count() == 0
        assert session.get(NodeRecord, "node-1").status == "dead"
