from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import update

from orchestrator.config import settings
from orchestrator.db import JobRecord, NodeRecord, SessionLocal, WorkspaceLockRecord
from orchestrator.scheduler import JobRequirements, available_gpu_vram, score_node
from shared.schemas import NodeHeartbeat, NodeRegister, new_id


class NoCapacityAvailable(RuntimeError):
    """Raised when no healthy node can reserve the requested resources."""


class IncompatibleNodeVersion(RuntimeError):
    """Raised when a node-agent version is outside the orchestrator compatibility window."""


def _version_tuple(value: str) -> tuple[int, int, int]:
    parts = []
    for token in str(value or "0.0.0").split(".")[:3]:
        number = "".join(ch for ch in token if ch.isdigit())
        parts.append(int(number or 0))
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)  # type: ignore[return-value]


def is_compatible_node_version(version: str) -> bool:
    current = _version_tuple(version)
    minimum = _version_tuple(settings.min_node_agent_version)
    return current >= minimum and current[0] == int(settings.max_node_agent_major_version)


def is_compatible_protocol_version(protocol_version: str) -> bool:
    """Return True when the node speaks the orchestrator scheduling protocol."""
    return str(protocol_version or "") >= str(settings.min_node_protocol_version)


def assert_node_compatible(agent_version: str, protocol_version: str) -> None:
    """Reject node-agent processes that cannot safely communicate with this orchestrator."""
    if not is_compatible_node_version(agent_version):
        raise IncompatibleNodeVersion(
            f"node-agent {agent_version} is not compatible with orchestrator minimum {settings.min_node_agent_version}"
        )
    if not is_compatible_protocol_version(protocol_version):
        raise IncompatibleNodeVersion(
            f"node protocol {protocol_version} is below required protocol {settings.min_node_protocol_version}"
        )


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _loads_list(value: str | None) -> list:
    if not value:
        return []
    try:
        data = json.loads(value)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _loads_dict(value: str | None) -> dict[str, float]:
    if not value:
        return {}
    try:
        data = json.loads(value)
        if not isinstance(data, dict):
            return {}
        return {str(key): float(amount) for key, amount in data.items()}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _vram_reservation_for_node(node: NodeRecord, requested_vram_gb: float) -> dict[str, float]:
    if requested_vram_gb <= 0:
        return {}
    candidates = sorted(node.gpus or [], key=lambda gpu: available_gpu_vram(node, gpu), reverse=True)
    for gpu in candidates:
        gpu_id = str(gpu.get("id") or "")
        if not gpu_id:
            continue
        if available_gpu_vram(node, gpu) >= requested_vram_gb:
            return {gpu_id: float(requested_vram_gb)}
    return {}


def register_node(payload: NodeRegister, base_url: str) -> NodeRecord:
    now = _utcnow()
    compatible = is_compatible_node_version(payload.agent_version) and is_compatible_protocol_version(
        payload.protocol_version
    )
    with SessionLocal() as session:
        node = session.get(NodeRecord, payload.node_id)
        if node is None:
            node = NodeRecord(id=payload.node_id)
            session.add(node)
        node.hostname = payload.hostname
        node.base_url = base_url.rstrip("/")
        node.status = "healthy" if compatible else "incompatible"
        node.cpu_total = payload.cpu_cores_total
        node.cpu_free = payload.cpu_cores_total
        node.ram_total_gb = payload.ram_gb_total
        node.ram_free_gb = payload.ram_gb_total
        node.gpu_json = json.dumps([gpu.model_dump(mode="json") for gpu in payload.gpus])
        node.capabilities_json = json.dumps(payload.capabilities)
        node.supported_tools_json = json.dumps(
            [tool.value if hasattr(tool, "value") else str(tool) for tool in payload.supported_tools]
        )
        node.docker_available = 1 if payload.docker_available else 0
        node.platform_arch = payload.platform_arch or "unknown"
        node.agent_version = payload.agent_version or "0.0.0"
        node.protocol_version = payload.protocol_version or "0.0.0"
        node.reserved_cpu_cores = 0
        node.reserved_ram_gb = 0.0
        node.reserved_vram_json = "{}"
        node.last_heartbeat = now
        session.commit()
        session.refresh(node)
        if not compatible:
            raise IncompatibleNodeVersion(
                f"node-agent {payload.agent_version} / protocol {payload.protocol_version} is incompatible with required node version {settings.min_node_agent_version} and protocol {settings.min_node_protocol_version}"
            )
        return node


def update_heartbeat(node_id: str, payload: NodeHeartbeat) -> NodeRecord | None:
    now = _utcnow()
    with SessionLocal() as session:
        node = session.get(NodeRecord, node_id)
        if node is None:
            return None
        compatible = is_compatible_node_version(payload.agent_version) and is_compatible_protocol_version(
            payload.protocol_version
        )
        if not compatible:
            node.status = "incompatible"
        elif node.status == "draining":
            node.status = "draining"
        else:
            node.status = payload.status
        node.cpu_free = payload.cpu_cores_free
        node.ram_free_gb = payload.ram_gb_free
        node.gpu_json = json.dumps([gpu.model_dump(mode="json") for gpu in payload.gpus])
        node.running_jobs = payload.running_jobs
        node.loaded_models_json = json.dumps(payload.loaded_models)
        node.capabilities_json = json.dumps(payload.capabilities or node.capabilities)
        node.supported_tools_json = json.dumps(
            [tool.value if hasattr(tool, "value") else str(tool) for tool in payload.supported_tools]
        )
        node.docker_available = 1 if payload.docker_available else 0
        node.platform_arch = payload.platform_arch or "unknown"
        node.agent_version = payload.agent_version or "0.0.0"
        node.protocol_version = payload.protocol_version or "0.0.0"
        node.last_heartbeat = now
        session.commit()
        session.refresh(node)
        return node


def list_nodes(status: str | None = None) -> list[NodeRecord]:
    with SessionLocal() as session:
        query = session.query(NodeRecord)
        if status:
            query = query.filter(NodeRecord.status == status)
        return list(query.order_by(NodeRecord.id).all())


def try_reserve_node(
    *,
    node_id: str,
    job_id: str,
    cpu: int,
    ram_gb: float,
    vram: dict[str, float],
    expected_version: int,
) -> bool:
    """Reserve node capacity if the node still has the expected version.

    The version predicate is the optimistic concurrency guard. A rowcount of zero
    means either capacity disappeared, health changed, or another scheduler won.
    """
    with SessionLocal() as session:
        stmt = (
            update(NodeRecord)
            .where(NodeRecord.id == node_id)
            .where(NodeRecord.version == expected_version)
            .where(NodeRecord.status == "healthy")
            .where(NodeRecord.cpu_free - NodeRecord.reserved_cpu_cores >= cpu)
            .where(NodeRecord.ram_free_gb - NodeRecord.reserved_ram_gb >= ram_gb)
            .values(
                reserved_cpu_cores=NodeRecord.reserved_cpu_cores + cpu,
                reserved_ram_gb=NodeRecord.reserved_ram_gb + ram_gb,
                version=NodeRecord.version + 1,
            )
        )
        result = session.execute(stmt)
        if result.rowcount != 1:
            session.rollback()
            return False

        node = session.get(NodeRecord, node_id)
        if node is None:
            session.rollback()
            return False
        reserved_vram = _loads_dict(node.reserved_vram_json)
        for gpu_id, amount in vram.items():
            reserved_vram[gpu_id] = reserved_vram.get(gpu_id, 0.0) + float(amount)
        node.reserved_vram_json = json.dumps(reserved_vram, sort_keys=True)
        session.commit()
        return True


def reserve_node_for_job(job_id: str, requirements: JobRequirements) -> NodeRecord:
    """Atomically reserve capacity on the best compatible healthy node for a job."""
    mark_stale_nodes()
    for _attempt in range(settings.max_reservation_retries):
        with SessionLocal() as session:
            nodes = list(session.query(NodeRecord).filter(NodeRecord.status == "healthy").all())
            ranked = [(score_node(node, requirements), node) for node in nodes]
            ranked = [(score, node) for score, node in ranked if score >= 0]
            ranked.sort(key=lambda item: item[0], reverse=True)
            candidates: list[tuple[str, int, dict[str, float]]] = []
            for _score, node in ranked:
                vram = _vram_reservation_for_node(node, requirements.min_vram_gb)
                if requirements.min_vram_gb and not vram:
                    continue
                candidates.append((node.id, node.version, vram))

        for node_id, expected_version, vram in candidates:
            success = try_reserve_node(
                node_id=node_id,
                job_id=job_id,
                cpu=requirements.min_cpu_cores,
                ram_gb=requirements.min_ram_gb,
                vram=vram,
                expected_version=expected_version,
            )
            if success:
                with SessionLocal() as session:
                    node = session.get(NodeRecord, node_id)
                    if node is None:
                        release_node_reservation(job_id)
                        break
                    session.expunge(node)
                    node._job_reserved_vram = vram
                    return node

    raise NoCapacityAvailable(f"No healthy node can reserve resources for job {job_id}")


def choose_node(requirements: JobRequirements) -> NodeRecord | None:
    """Return the best currently available node without reserving it.

    New dispatch paths should use create_reserved_job(). This read-only helper is
    kept for compatibility and diagnostics.
    """
    mark_stale_nodes()
    with SessionLocal() as session:
        nodes = list(session.query(NodeRecord).filter(NodeRecord.status == "healthy").all())
        if not nodes:
            return None
        scored = [(score_node(node, requirements), node) for node in nodes]
        scored = [(score, node) for score, node in scored if score >= 0]
        if not scored:
            return None
        scored.sort(key=lambda item: item[0], reverse=True)
        selected = scored[0][1]
        session.expunge(selected)
        return selected


def _task_correlation_id(session: Any, task_id: str) -> str | None:
    from orchestrator.db import TaskRecord

    task = session.get(TaskRecord, task_id)
    return task.correlation_id if task else task_id


def _job_duration_ms(job: JobRecord, now: datetime | None = None) -> int:
    if not job.started_at:
        return 0
    now = now or _utcnow()
    started = job.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return max(0, int((now - started).total_seconds() * 1000))


def create_reserved_job(
    task_id: str,
    requirements: JobRequirements,
    subtask_id: str | None = None,
    job_type: str | None = None,
) -> tuple[str, NodeRecord | None]:
    """Create a job row and attach an active node reservation when capacity exists."""
    job_id = new_id("job")
    try:
        node = reserve_node_for_job(job_id, requirements)
    except NoCapacityAvailable:
        with SessionLocal() as session:
            session.add(
                JobRecord(
                    id=job_id,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    job_type=job_type or requirements.job_type,
                    status="queued",
                    correlation_id=_task_correlation_id(session, task_id),
                )
            )
            session.commit()
        return job_id, None

    with SessionLocal() as session:
        now = _utcnow()
        node_row = session.get(NodeRecord, node.id)
        if node_row:
            node_row.running_jobs += 1
        session.add(
            JobRecord(
                id=job_id,
                task_id=task_id,
                subtask_id=subtask_id,
                job_type=job_type or requirements.job_type,
                status="running",
                assigned_node_id=node.id,
                started_at=now,
                attempt_id=new_id("attempt"),
                lease_expires_at=now + timedelta(seconds=settings.job_lease_seconds),
                generation=1,
                reserved_cpu_cores=requirements.min_cpu_cores,
                reserved_ram_gb=requirements.min_ram_gb,
                reserved_vram_json=json.dumps(getattr(node, "_job_reserved_vram", {}), sort_keys=True),
                correlation_id=_task_correlation_id(session, task_id),
            )
        )
        session.commit()
    return job_id, node


def release_node_reservation(job_id: str) -> None:
    """Return CPU, RAM, and VRAM capacity reserved by a job to its node."""
    with SessionLocal() as session:
        job = session.get(JobRecord, job_id)
        if not job or not job.assigned_node_id:
            return
        node = session.get(NodeRecord, job.assigned_node_id)
        if node:
            node.reserved_cpu_cores = max(0, node.reserved_cpu_cores - int(job.reserved_cpu_cores or 0))
            node.reserved_ram_gb = max(0.0, node.reserved_ram_gb - float(job.reserved_ram_gb or 0))
            reserved_vram = _loads_dict(node.reserved_vram_json)
            for gpu_id, amount in _loads_dict(job.reserved_vram_json).items():
                remaining = reserved_vram.get(gpu_id, 0.0) - amount
                if remaining > 0:
                    reserved_vram[gpu_id] = remaining
                else:
                    reserved_vram.pop(gpu_id, None)
            node.reserved_vram_json = json.dumps(reserved_vram, sort_keys=True)
            node.version += 1
        job.reserved_cpu_cores = 0
        job.reserved_ram_gb = 0
        job.reserved_vram_json = "{}"
        session.commit()


def finish_job(job_id: str, status: str = "done") -> None:
    """Move a running job to a canonical terminal status and release resources."""
    with SessionLocal() as session:
        job = session.get(JobRecord, job_id)
        if not job:
            return
        old_status = job.status
        job.status = status
        job.finished_at = _utcnow()
        job.duration_ms = _job_duration_ms(job, job.finished_at)
        if status in {"failed", "lost"}:
            job.error_code = "JOB_LOST" if status == "lost" else "JOB_FAILED"
        if job.assigned_node_id and old_status == "running":
            node = session.get(NodeRecord, job.assigned_node_id)
            if node:
                node.running_jobs = max(0, node.running_jobs - 1)
        session.commit()
    release_node_reservation(job_id)


def set_node_draining(node_id: str, draining: bool = True) -> NodeRecord | None:
    with SessionLocal() as session:
        node = session.get(NodeRecord, node_id)
        if node is None:
            return None
        if node.status not in {"dead", "incompatible"}:
            node.status = "draining" if draining else "healthy"
            node.version += 1
        session.commit()
        session.refresh(node)
        return node


def mark_stale_nodes() -> list[JobRecord]:
    """Mark unreachable/dead nodes and convert their running jobs to lost."""
    now = _utcnow()
    unreachable_before = now - timedelta(seconds=settings.node_unreachable_after_seconds)
    dead_before = now - timedelta(seconds=settings.node_dead_after_seconds)
    lost_jobs: list[JobRecord] = []
    with SessionLocal() as session:
        for node in session.query(NodeRecord).all():
            last = node.last_heartbeat
            if last is None:
                continue
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            if node.status == "incompatible":
                continue
            if last <= dead_before:
                node.status = "dead"
            elif last <= unreachable_before and node.status == "healthy":
                node.status = "unreachable"
            if node.status == "dead":
                running = (
                    session.query(JobRecord)
                    .filter(JobRecord.assigned_node_id == node.id, JobRecord.status == "running")
                    .all()
                )
                for job in running:
                    job.status = "lost"
                    job.lost_at = now
                    job.error_code = "NODE_STALE_JOB_LOST"
                    job.duration_ms = _job_duration_ms(job, now)
                    job.reserved_cpu_cores = 0
                    job.reserved_ram_gb = 0
                    job.reserved_vram_json = "{}"
                    lost_jobs.append(job)
                    for lock in session.query(WorkspaceLockRecord).filter(WorkspaceLockRecord.job_id == job.id).all():
                        session.delete(lock)
                node.running_jobs = 0
                node.reserved_cpu_cores = 0
                node.reserved_ram_gb = 0
                node.reserved_vram_json = "{}"
                node.version += 1
        session.commit()
    return lost_jobs


def get_job_execution_context(job_id: str) -> dict[str, Any]:
    """Return the lease metadata that must be echoed by the node result."""
    with SessionLocal() as session:
        job = session.get(JobRecord, job_id)
        if job is None:
            raise KeyError(f"job not found: {job_id}")
        return {
            "job_id": job.id,
            "attempt_id": job.attempt_id,
            "generation": job.generation,
            "lease_expires_at": job.lease_expires_at,
            "assigned_node_id": job.assigned_node_id,
        }


def attach_execution_context(request: Any, job_id: str) -> Any:
    """Mutate a pydantic job request so stale result checks can validate it."""
    ctx = get_job_execution_context(job_id)
    request.job_id = job_id
    request.attempt_id = ctx["attempt_id"]
    request.generation = ctx["generation"]
    request.lease_expires_at = ctx["lease_expires_at"]
    return request


def accept_job_result(
    job_id: str,
    attempt_id: str | None,
    node_id: str | None,
    generation: int | None,
    result: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Validate result ownership, lease freshness, and generation before accepting output."""
    now = _utcnow()
    with SessionLocal() as session:
        job = session.get(JobRecord, job_id)
        if job is None:
            return False, "job does not exist"
        if job.status != "running":
            return False, "job is not running"
        if job.assigned_node_id != node_id:
            return False, "wrong node"
        if not job.attempt_id or job.attempt_id != attempt_id:
            return False, "stale attempt"
        if int(job.generation or 0) != int(generation or 0):
            return False, "stale generation"
        lease = job.lease_expires_at
        if lease is None:
            return False, "missing lease"
        if lease.tzinfo is None:
            lease = lease.replace(tzinfo=UTC)
        if now > lease:
            return False, "lease expired"
        if result is not None:
            job.result_json = json.dumps(result, default=str)
        session.commit()
        return True, "accepted"


def finish_job_from_result(job_id: str, result: Any, status: str) -> tuple[bool, str]:
    """Finish a job only when its echoed execution context passes stale-result checks."""
    payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result or {})
    accepted, reason = accept_job_result(
        job_id=job_id,
        attempt_id=payload.get("attempt_id"),
        node_id=payload.get("node_id"),
        generation=payload.get("generation"),
        result=payload,
    )
    if accepted:
        finish_job(job_id, status)
    return accepted, reason


def cleanup_expired_job_leases() -> list[JobRecord]:
    """Recover jobs whose node lease expired without a valid result."""
    now = _utcnow()
    expired: list[JobRecord] = []
    with SessionLocal() as session:
        rows = session.query(JobRecord).filter(JobRecord.status == "running").all()
        for job in rows:
            lease = job.lease_expires_at
            if lease is None:
                continue
            if lease.tzinfo is None:
                lease = lease.replace(tzinfo=UTC)
            if lease <= now:
                job.status = "lost"
                job.lost_at = now
                job.error_code = "JOB_LEASE_EXPIRED"
                job.duration_ms = _job_duration_ms(job, now)
                if job.assigned_node_id:
                    node = session.get(NodeRecord, job.assigned_node_id)
                    if node:
                        node.running_jobs = max(0, node.running_jobs - 1)
                expired.append(job)
        session.commit()
    for job in expired:
        release_node_reservation(job.id)
    return expired


def _drop_expired_workspace_locks(session: Any, workspace_id: str | None = None) -> None:
    now = _utcnow()
    query = session.query(WorkspaceLockRecord).filter(WorkspaceLockRecord.expires_at <= now)
    if workspace_id:
        query = query.filter(WorkspaceLockRecord.workspace_id == workspace_id)
    for row in query.all():
        session.delete(row)


def acquire_workspace_lock(workspace_id: str, lock_type: str, job_id: str, ttl_seconds: int | None = None) -> bool:
    """Acquire a read/write reservation for a workspace while pruning expired locks."""
    if lock_type not in {"read", "write"}:
        raise ValueError("lock_type must be read or write")
    expires_at = _utcnow() + timedelta(seconds=ttl_seconds or settings.workspace_lock_seconds)
    with SessionLocal() as session:
        _drop_expired_workspace_locks(session, workspace_id)
        active = session.query(WorkspaceLockRecord).filter(WorkspaceLockRecord.workspace_id == workspace_id).all()
        if lock_type == "read":
            conflict = any(row.lock_type == "write" and row.job_id != job_id for row in active)
        else:
            conflict = any(row.job_id != job_id for row in active)
        if conflict:
            session.rollback()
            return False
        existing = session.get(
            WorkspaceLockRecord, {"workspace_id": workspace_id, "lock_type": lock_type, "job_id": job_id}
        )
        if existing:
            existing.expires_at = expires_at
        else:
            session.add(
                WorkspaceLockRecord(
                    workspace_id=workspace_id, lock_type=lock_type, job_id=job_id, expires_at=expires_at
                )
            )
        session.commit()
        return True


def release_workspace_lock(job_id: str, workspace_id: str | None = None) -> None:
    """Release workspace reservations held by a job."""
    with SessionLocal() as session:
        query = session.query(WorkspaceLockRecord).filter(WorkspaceLockRecord.job_id == job_id)
        if workspace_id:
            query = query.filter(WorkspaceLockRecord.workspace_id == workspace_id)
        for row in query.all():
            session.delete(row)
        session.commit()


def node_to_dict(node: NodeRecord) -> dict:
    return {
        "node_id": node.id,
        "hostname": node.hostname,
        "base_url": node.base_url,
        "status": node.status,
        "cpu_cores_total": node.cpu_total,
        "cpu_cores_free": node.cpu_free,
        "ram_gb_total": node.ram_total_gb,
        "ram_gb_free": node.ram_free_gb,
        "gpus": _loads_list(node.gpu_json),
        "capabilities": _loads_list(node.capabilities_json),
        "supported_tools": _loads_list(node.supported_tools_json),
        "docker_available": bool(node.docker_available),
        "platform_arch": node.platform_arch,
        "agent_version": node.agent_version,
        "protocol_version": getattr(node, "protocol_version", "2026-06-14"),
        "loaded_models": _loads_list(node.loaded_models_json),
        "running_jobs": node.running_jobs,
        "reserved_cpu_cores": node.reserved_cpu_cores,
        "reserved_ram_gb": node.reserved_ram_gb,
        "reserved_vram": _loads_dict(node.reserved_vram_json),
        "version": node.version,
        "last_heartbeat": node.last_heartbeat,
    }
