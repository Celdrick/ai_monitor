from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

AgentStatus = Literal["online", "offline", "never"]


class AgentCreate(BaseModel):
    host: str = Field(min_length=1, max_length=255)


class AgentWithToken(BaseModel):
    id: int
    host: str
    token: str


class HeartbeatRequest(BaseModel):
    host: str
    ip: str | None = None
    listen_port: int | None = None
    advertise_address: str | None = None
    agent_version: str | None = None
    hardware_vendor: str | None = None
    device_count: int = 0
    services: list[Any] = []


class AgentOut(BaseModel):
    id: int
    host: str
    ip: str | None
    advertise_address: str | None
    version: str | None
    hardware_vendor: str | None
    device_count: int
    last_seen_at: datetime | None
    status: AgentStatus
