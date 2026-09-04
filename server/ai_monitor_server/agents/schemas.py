from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AgentStatus = Literal["online", "offline", "never"]


class ServiceIn(BaseModel):
    """One entry of the heartbeat ``services`` list.

    Field names mirror the agent's ``ServiceInfo.to_dict()`` exactly; unknown
    fields are ignored so agent and server can evolve independently.
    """

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=255)
    source: str
    port: int
    metrics_url: str
    pid: int | None = None
    container_id: str | None = None
    container_name: str | None = None
    log_source: str = "none"
    log_path: str | None = None
    profiler_dir: str | None = None
    model: str | None = None
    vllm_version: str | None = None
    started_at: float | None = None
    cmdline: str | None = None
    cwd: str | None = None
    env: dict[str, str] = {}
    scrape_ok: bool = False


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
    services: list[ServiceIn] = []


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
