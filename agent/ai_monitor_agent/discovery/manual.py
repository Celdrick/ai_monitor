"""Manually registered services from ``services:`` in agent.yaml."""

from __future__ import annotations

from ..config import ManualService
from ..services import ServiceInfo


class ManualDiscovery:
    name = "manual"

    def __init__(self, services: list[ManualService]) -> None:
        self._services = list(services)

    def discover(self) -> list[ServiceInfo]:
        return [
            ServiceInfo(
                name=m.name,
                source="manual",
                port=m.port,
                metrics_url=f"http://127.0.0.1:{m.port}",
                log_source="file" if m.log_path else "none",
                log_path=m.log_path,
                profiler_dir=m.profiler_dir,
            )
            for m in self._services
        ]
