"""memory system

Revision ID: 0005_memory_system
Revises: 0004_api_design
Create Date: 2026-06-08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_memory_system"
down_revision = "0004_api_design"
branch_labels = None
depends_on = None


def _safe(action) -> None:
    try:
        action()
    except Exception:
        pass


def upgrade() -> None:
    _safe(lambda: op.drop_constraint("uq_memory_scope_key", "memories", type_="unique"))
    _safe(lambda: op.add_column("memories", sa.Column("namespace", sa.String(length=32), nullable=False, server_default="global")))
    _safe(lambda: op.add_column("memories", sa.Column("user_id", sa.String(length=128), nullable=True)))
    _safe(lambda: op.add_column("memories", sa.Column("project_id", sa.String(length=128), nullable=True)))
    _safe(lambda: op.add_column("memories", sa.Column("provenance", sa.String(length=32), nullable=False, server_default="operator_entry")))
    _safe(lambda: op.add_column("memories", sa.Column("provenance_ref", sa.Text(), nullable=True)))
    _safe(lambda: op.add_column("memories", sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0")))
    _safe(lambda: op.add_column("memories", sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())))
    _safe(lambda: op.add_column("memories", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True)))
    _safe(lambda: op.add_column("memories", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)))
    _safe(lambda: op.add_column("memories", sa.Column("version", sa.Integer(), nullable=False, server_default="1")))
    _safe(lambda: op.create_unique_constraint("uq_memory_namespace_scope_key", "memories", ["namespace", "scope", "key"]))
    _safe(lambda: op.create_check_constraint("ck_memories_namespace_valid", "memories", "namespace IN ('global', 'task', 'user', 'project', 'temporary')"))
    _safe(lambda: op.create_check_constraint("ck_memories_provenance_valid", "memories", "provenance IN ('task_result', 'user_input', 'operator_entry', 'model_summary')"))
    _safe(lambda: op.create_check_constraint("ck_memories_confidence_range", "memories", "confidence >= 0 AND confidence <= 1"))
    _safe(lambda: op.create_index("ix_memories_namespace", "memories", ["namespace"]))
    _safe(lambda: op.create_index("ix_memories_expires_at", "memories", ["expires_at"]))
    _safe(lambda: op.create_index("ix_memories_observed_at", "memories", ["observed_at"]))


def downgrade() -> None:
    _safe(lambda: op.drop_index("ix_memories_observed_at", table_name="memories"))
    _safe(lambda: op.drop_index("ix_memories_expires_at", table_name="memories"))
    _safe(lambda: op.drop_index("ix_memories_namespace", table_name="memories"))
    _safe(lambda: op.drop_constraint("ck_memories_confidence_range", "memories", type_="check"))
    _safe(lambda: op.drop_constraint("ck_memories_provenance_valid", "memories", type_="check"))
    _safe(lambda: op.drop_constraint("ck_memories_namespace_valid", "memories", type_="check"))
    _safe(lambda: op.drop_constraint("uq_memory_namespace_scope_key", "memories", type_="unique"))
    for column in ["version", "deleted_at", "expires_at", "observed_at", "confidence", "provenance_ref", "provenance", "project_id", "user_id", "namespace"]:
        _safe(lambda column=column: op.drop_column("memories", column))
    _safe(lambda: op.create_unique_constraint("uq_memory_scope_key", "memories", ["scope", "key"]))
