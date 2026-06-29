"""node agent reliability

Revision ID: 0003_node_agent_reliability
Revises: 0002_observability_audit
Create Date: 2026-06-08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_node_agent_reliability"
down_revision = "0002_observability_audit"
branch_labels = None
depends_on = None


def _safe(action) -> None:
    try:
        action()
    except Exception:
        pass

def upgrade() -> None:
    try:
        op.drop_constraint("ck_nodes_status_valid", "nodes", type_="check")
    except Exception:
        pass
    _safe(lambda: op.create_check_constraint("ck_nodes_status_valid", "nodes", "status IN ('healthy', 'unreachable', 'dead', 'recovered', 'draining', 'incompatible')"))
    _safe(lambda: op.add_column("nodes", sa.Column("supported_tools_json", sa.Text(), nullable=False, server_default="[]")))
    _safe(lambda: op.add_column("nodes", sa.Column("docker_available", sa.Integer(), nullable=False, server_default="0")))
    _safe(lambda: op.add_column("nodes", sa.Column("platform_arch", sa.String(length=64), nullable=False, server_default="unknown")))
    _safe(lambda: op.add_column("nodes", sa.Column("agent_version", sa.String(length=64), nullable=False, server_default="0.0.0")))
    _safe(lambda: op.create_check_constraint("ck_nodes_docker_available_bool", "nodes", "docker_available IN (0, 1)"))


def downgrade() -> None:
    try:
        op.drop_constraint("ck_nodes_status_valid", "nodes", type_="check")
    except Exception:
        pass
    op.create_check_constraint("ck_nodes_status_valid", "nodes", "status IN ('healthy', 'unreachable', 'dead', 'recovered')")
    op.drop_constraint("ck_nodes_docker_available_bool", "nodes", type_="check")
    op.drop_column("nodes", "agent_version")
    op.drop_column("nodes", "platform_arch")
    op.drop_column("nodes", "docker_available")
    op.drop_column("nodes", "supported_tools_json")
