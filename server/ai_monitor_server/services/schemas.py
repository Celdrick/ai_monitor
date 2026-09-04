from datetime import datetime
from typing import Literal

from pydantic import BaseModel

ServiceStatus = Literal["running", "degraded", "stopped", "unknown"]


class ServiceOut(BaseModel):
    id: int
    agent_id: int
    host: str
    name: str
    source: str
    port: int
    metrics_url: str
    pid: int | None
    container_name: str | None
    model: str | None
    vllm_version: str | None
    started_at: datetime | None
    log_source: str
    scrape_ok: bool
    active: bool
    last_seen_at: datetime | None
    status: ServiceStatus


class ServiceDetail(ServiceOut):
    cmdline: str | None
    cwd: str | None
    env: dict[str, str]
    container_id: str | None
    log_path: str | None
    profiler_dir: str | None
    first_seen_at: datetime
