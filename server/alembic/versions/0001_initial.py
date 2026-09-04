"""initial: users and agents

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )
    op.create_table(
        "agents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("advertise_address", sa.String(length=255), nullable=True),
        sa.Column("listen_port", sa.Integer(), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=True),
        sa.Column("hardware_vendor", sa.String(length=32), nullable=True),
        sa.Column("device_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("host", name="uq_agents_host"),
        sa.UniqueConstraint("token_hash", name="uq_agents_token_hash"),
    )


def downgrade() -> None:
    op.drop_table("agents")
    op.drop_table("users")
