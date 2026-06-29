from __future__ import annotations

import logging
import time

from orchestrator.config import settings
from orchestrator.db import SessionLocal, TaskRecord, init_db
from orchestrator.lifecycle import WorkerShutdown, close_orchestrator_resources
from orchestrator.registry import cleanup_expired_job_leases, mark_stale_nodes
from orchestrator.state_machine import TaskRunner
from orchestrator.task_queue import RedisTaskQueue
from shared.logging import configure_logging, log_event
from shared.schemas import TaskStatus

configure_logging(settings.log_level)
logger = logging.getLogger(__name__)
queue = RedisTaskQueue()
runner = TaskRunner()
shutdown_state = WorkerShutdown()


def requeue_lost_jobs() -> None:
    lost_jobs = mark_stale_nodes() + cleanup_expired_job_leases()
    if not lost_jobs:
        return
    for job in lost_jobs:
        log_event(logger, "node_lost", task_id=job.task_id, job_id=job.id, node_id=job.assigned_node_id)
    task_ids = sorted({job.task_id for job in lost_jobs})
    with SessionLocal() as session:
        for task_id in task_ids:
            task = session.get(TaskRecord, task_id)
            if task and task.status not in {TaskStatus.done.value, TaskStatus.failed.value}:
                task.status = TaskStatus.requeued.value
                task.current_phase = "requeued:node_lost"
                queue.enqueue(task_id, priority=task.priority)
                log_event(logger, "job_requeued", task_id=task_id)
        session.commit()


def run_forever() -> None:
    init_db()
    shutdown_state.install()
    log_event(logger, "queue_worker_started")
    while not shutdown_state.should_stop():
        try:
            requeue_lost_jobs()
            item = queue.get_next(timeout=settings.worker_poll_timeout_seconds)
            if shutdown_state.should_stop():
                if item:
                    queue.enqueue(item["task_id"], priority=int(item.get("priority", 5)))
                break
            if item:
                runner.run_task(item["task_id"])
        except Exception as exc:
            log_event(logger, "queue_worker_error", error=str(exc))
            time.sleep(2)
    close_orchestrator_resources(queue)
    log_event(logger, "queue_worker_stopped")


def main() -> None:
    run_forever()


if __name__ == "__main__":
    main()
