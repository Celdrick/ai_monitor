"""Agent configuration model and YAML loader."""

from __future__ import annotations

import yaml
from pydantic import BaseModel, Field


class ManualService(BaseModel):
    """A manually registered (nohup/tmux) vLLM service."""

    name: str
    port: int
    log_path: str | None = None
    profiler_dir: str | None = None


class AgentConfig(BaseModel):
    server_url: str
    agent_token: str
    loki_url: str | None = None
    listen: str = "0.0.0.0:9400"
    advertise_address: str | None = None  # "ip:port"; auto-detected when None
    scrape_interval_seconds: int = 15
    heartbeat_interval_seconds: int = 30
    services: list[ManualService] = Field(default_factory=list)
    artifacts_dir: str = "/var/lib/ai-monitor-agent/artifacts"

    @property
    def listen_host(self) -> str:
        host, _, _port = self.listen.rpartition(":")
        return host or "0.0.0.0"

    @property
    def listen_port(self) -> int:
        _host, sep, port = self.listen.rpartition(":")
        if not sep:
            return 9400
        return int(port)


def load_config(path: str) -> AgentConfig:
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return AgentConfig.model_validate(data)
