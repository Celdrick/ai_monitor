"""Service model shared by discovery, metrics proxy, log tailing and heartbeat."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Source = Literal["docker", "process", "manual"]
LogSource = Literal["docker", "file", "none"]


@dataclass
class ServiceInfo:
    name: str
    source: Source
    port: int
    metrics_url: str
    pid: int | None = None
    container_id: str | None = None
    container_name: str | None = None
    log_source: LogSource = "none"
    log_path: str | None = None
    profiler_dir: str | None = None
    model: str | None = None
    vllm_version: str | None = None
    started_at: float | None = None
    cmdline: str | None = None
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    scrape_ok: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Plain dict matching the server's ``ServiceIn`` schema field-for-field."""
        return asdict(self)
