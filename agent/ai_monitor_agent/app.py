"""FastAPI application exposing /metrics and /healthz, with heartbeat lifespan."""

from __future__ import annotations

import asyncio
import logging
import socket
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST

from .collectors.base import DeviceCollector
from .collectors.host import HostCollector
from .config import AgentConfig
from .heartbeat import HeartbeatClient
from .metrics import AgentMetricsCollector, build_registry, render

log = logging.getLogger(__name__)


def _make_inventory(device_collectors: list[DeviceCollector]):
    """Return a callable producing the heartbeat inventory dict."""

    def inventory() -> dict[str, Any]:
        vendor = "none"
        count = 0
        for collector in device_collectors:
            try:
                samples = collector.collect()
            except Exception as exc:
                log.debug("inventory: collector %s failed: %s", collector.name, exc)
                continue
            if samples:
                count += len(samples)
                if vendor == "none":
                    vendor = samples[0].vendor
        return {"hardware_vendor": vendor, "device_count": count, "services": []}

    return inventory


def create_app(
    config: AgentConfig,
    device_collectors: list[DeviceCollector],
    host_collector: HostCollector | None,
    host: str | None = None,
) -> FastAPI:
    host = host or socket.gethostname()
    metrics_collector = AgentMetricsCollector(host, device_collectors, host_collector)
    registry = build_registry(metrics_collector)
    heartbeat = HeartbeatClient(config, host, _make_inventory(device_collectors))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = asyncio.Event()
        task = asyncio.create_task(heartbeat.run(stop), name="heartbeat")
        app.state.heartbeat_stop = stop
        app.state.heartbeat_task = task
        try:
            yield
        finally:
            stop.set()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    app = FastAPI(title="ai-monitor-agent", lifespan=lifespan)
    app.state.config = config
    app.state.host = host
    app.state.registry = registry
    app.state.heartbeat = heartbeat

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(content=render(registry), media_type=CONTENT_TYPE_LATEST)

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        collectors: dict[str, bool] = {}
        for c in device_collectors:
            try:
                collectors[c.name] = bool(c.available())
            except Exception:
                collectors[c.name] = False
        if host_collector is not None:
            collectors[host_collector.name] = True
        return {"status": "ok", "host": host, "collectors": collectors}

    return app
