"""api design

Revision ID: 0004_api_design
Revises: 0003_node_agent_reliability
Create Date: 2026-06-08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_api_design"
down_revision = "0003_node_agent_reliability"
branch_labels = None
depends_on = None


def _safe(action) -> None:
    try:
        action()
    except Exception:
        pass


def upgrade() -> None:
    try:
        op.drop_constraint("ck_tasks_status_valid", "tasks", type_="check")
    except Exception:
        pass
    _safe(lambda: op.create_check_constraint(
        "ck_tasks_status_valid",
        "tasks",
        "status IN ('queued', 'planning', 'delegating', 'running_agent', 'running_tool', 'evaluating', 'optimizing', 'final_check', 'refining', 'completed', 'done', 'verified', 'unverified', 'failed', 'requeued', 'canceled')",
    ))
    _safe(lambda: op.add_column("tasks", sa.Column("idempotency_key", sa.String(length=255), nullable=True)))
    _safe(lambda: op.create_index("ix_tasks_idempotency_key", "tasks", ["idempotency_key"]))
    _safe(lambda: op.create_unique_constraint("uq_tasks_idempotency_key", "tasks", ["idempotency_key"]))
    _safe(lambda: op.add_column("jobs", sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())))
    _safe(lambda: op.add_column("jobs", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())))
    _safe(lambda: op.create_index("ix_jobs_created_at", "jobs", ["created_at"]))


def downgrade() -> None:
    op.drop_index("ix_jobs_created_at", table_name="jobs")
    op.drop_column("jobs", "updated_at")
    op.drop_column("jobs", "created_at")
    op.drop_constraint("uq_tasks_idempotency_key", "tasks", type_="unique")
    op.drop_index("ix_tasks_idempotency_key", table_name="tasks")
    op.drop_column("tasks", "idempotency_key")
    try:
        op.drop_constraint("ck_tasks_status_valid", "tasks", type_="check")
    except Exception:
        pass
    op.create_check_constraint(
        "ck_tasks_status_valid",
        "tasks",
        "status IN ('queued', 'planning', 'delegating', 'running_agent', 'running_tool', 'evaluating', 'optimizing', 'final_check', 'refining', 'completed', 'done', 'verified', 'unverified', 'failed', 'requeued')",
    )
