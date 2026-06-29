"""Step 6.17 deployment readiness runtime fields."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0008_step617_deployment_readiness"
down_revision = "0005_memory_system"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("nodes", sa.Column("protocol_version", sa.String(length=64), nullable=False, server_default="2026-06-14"))


def downgrade() -> None:
    op.drop_column("nodes", "protocol_version")
