"""services: vLLM services discovered by agents

Revision ID: 0002_services
Revises: 0001_initial
Create Date: 2026-09-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_services"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "services",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "agent_id",
            sa.Integer(),
            sa.ForeignKey("agents.id", ondelete="CASCADE", name="fk_services_agent_id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("metrics_url", sa.String(length=512), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("container_id", sa.String(length=128), nullable=True),
        sa.Column("container_name", sa.String(length=255), nullable=True),
        sa.Column("log_source", sa.String(length=16), nullable=False, server_default="none"),
        sa.Column("log_path", sa.String(length=1024), nullable=True),
        sa.Column("profiler_dir", sa.String(length=1024), nullable=True),
        sa.Column("model", sa.String(length=512), nullable=True),
        sa.Column("vllm_version", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cmdline", sa.Text(), nullable=True),
        sa.Column("cwd", sa.String(length=1024), nullable=True),
        sa.Column("env_json", sa.JSON(), nullable=False),
        sa.Column("scrape_ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("agent_id", "name", name="uq_services_agent_name"),
    )
    op.create_index("ix_services_agent_id", "services", ["agent_id"])


def downgrade() -> None:
    op.drop_index("ix_services_agent_id", table_name="services")
    op.drop_table("services")
