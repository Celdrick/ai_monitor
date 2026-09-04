from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String
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
