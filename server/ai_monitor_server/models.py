from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="viewer")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    host: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    advertise_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    listen_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hardware_vendor: Mapped[str | None] = mapped_column(String(32), nullable=True)
    device_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class Service(Base):
    __tablename__ = "services"
    __table_args__ = (UniqueConstraint("agent_id", "name", name="uq_services_agent_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    metrics_url: Mapped[str] = mapped_column(String(512), nullable=False)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    container_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    container_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    log_source: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    log_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    profiler_dir: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    model: Mapped[str | None] = mapped_column(String(512), nullable=True)
    vllm_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cmdline: Mapped[str | None] = mapped_column(Text, nullable=True)
    cwd: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    env_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    scrape_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
