from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from orchestrator import registry
from orchestrator.db import Base, JobRecord, NodeRecord
from orchestrator.scheduler import JobRequirements, score_node


def _test_session(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'reservations.db'}")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr(registry, "SessionLocal", Session)
    return Session


def _add_node(Session, node_id="node-1", cpu=4, ram=8.0, vram=0.0):
    with Session() as session:
        session.add(
            NodeRecord(
                id=node_id,
                hostname=node_id,
                base_url=f"http://{node_id}",
                status="healthy",
                cpu_total=cpu,
                cpu_free=cpu,
                ram_total_gb=ram,
                ram_free_gb=ram,
                gpu_json=json.dumps([{"id": "gpu0", "vram_gb_free": vram}]) if vram else "[]",
                capabilities_json=json.dumps(["cpu_worker", "tool_shell"]),
                loaded_models_json="[]",
                last_heartbeat=datetime.now(UTC),
            )
        )
        session.commit()


def test_reservation_prevents_overbooking_and_release_restores_capacity(tmp_path, monkeypatch):
    Session = _test_session(tmp_path, monkeypatch)
    _add_node(Session)
    req = JobRequirements("cpu_worker", min_cpu_cores=4, min_ram_gb=8, required_capability="cpu_worker")

    first_job, first_node = registry.create_reserved_job("task-1", req)
    second_job, second_node = registry.create_reserved_job("task-2", req)

    assert first_node is not None
    assert second_node is None
    with Session() as session:
        node = session.get(NodeRecord, "node-1")
        assert node.reserved_cpu_cores == 4
        assert node.reserved_ram_gb == 8
        assert session.get(JobRecord, second_job).status == "queued"
        assert score_node(node, req) == -1

    registry.finish_job(first_job)

    with Session() as session:
        node = session.get(NodeRecord, "node-1")
        assert node.reserved_cpu_cores == 0
        assert node.reserved_ram_gb == 0
        assert score_node(node, req) >= 0


def test_vram_reservation_uses_gpu_id_and_blocks_conflict(tmp_path, monkeypatch):
    Session = _test_session(tmp_path, monkeypatch)
    _add_node(Session, cpu=8, ram=32.0, vram=12.0)
    req = JobRequirements("gpu_worker", requires_gpu=True, min_cpu_cores=2, min_ram_gb=4, min_vram_gb=10)

    first_job, first_node = registry.create_reserved_job("task-1", req)
    second_job, second_node = registry.create_reserved_job("task-2", req)

    assert first_node is not None
    assert second_node is None
    with Session() as session:
        node = session.get(NodeRecord, "node-1")
        assert json.loads(node.reserved_vram_json) == {"gpu0": 10.0}

    registry.finish_job(first_job)
    with Session() as session:
        node = session.get(NodeRecord, "node-1")
        assert json.loads(node.reserved_vram_json) == {}
