"""FastAPI application exposing /metrics and /healthz, with heartbeat lifespan."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST

from .collectors.base import DeviceCollector
from .collectors.host import HostCollector
from .config import AgentConfig
from .discovery.manual import ManualDiscovery
from .discovery.registry import ServiceRegistry
from .heartbeat import HeartbeatClient
from .logs.cursors import CursorStore
from .logs.loki_client import LokiPusher
from .logs.tailer import LogManager
from .metrics import AgentMetricsCollector, build_registry, render
from .vllm.metrics_proxy import VllmMetricsProxy

log = logging.getLogger(__name__)


def _make_inventory(device_collectors: list[DeviceCollector], svc_registry: ServiceRegistry):
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
        return {
            "hardware_vendor": vendor,
            "device_count": count,
            "services": [s.to_dict() for s in svc_registry.snapshot()],
        }

    return inventory


def create_app(
    config: AgentConfig,
    device_collectors: list[DeviceCollector],
    host_collector: HostCollector | None,
    host: str | None = None,
    registry: ServiceRegistry | None = None,
    fake_vllm: list | None = None,
) -> FastAPI:
    host = host or socket.gethostname()
    fake_vllm = list(fake_vllm or [])
    svc_registry = registry or ServiceRegistry([ManualDiscovery(config.services)])
    proxy = VllmMetricsProxy(svc_registry, host)
    logs_enabled = bool(config.loki_url) and config.logs.enabled
    pusher: LokiPusher | None = None
    cursors: CursorStore | None = None
    if logs_enabled:
        pusher = LokiPusher(
            loki_url=config.loki_url or "",
            host=host,
            batch_lines=config.logs.batch_lines,
            batch_interval=config.logs.batch_interval_seconds,
            buffer_max_lines=config.logs.buffer_max_lines,
        )
        cursors = CursorStore(os.path.join(config.state_dir, "log_cursors.json"))
    metrics_collector = AgentMetricsCollector(
        host, device_collectors, host_collector, proxy=proxy, pusher=pusher
    )
    prom_registry = build_registry(metrics_collector)
    heartbeat = HeartbeatClient(config, host, _make_inventory(device_collectors, svc_registry))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            os.makedirs(config.state_dir, exist_ok=True)
        except OSError as exc:
            log.warning("cannot create state_dir %s: %s", config.state_dir, exc)

        stop = asyncio.Event()
        tasks: list[asyncio.Task] = []
        loki_client: httpx.AsyncClient | None = None

        async def refresh_loop() -> None:
            while not stop.is_set():
                try:
                    await asyncio.to_thread(svc_registry.refresh)
                except Exception as exc:
                    log.warning("service registry refresh failed: %s", exc)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=config.discovery.interval_seconds)
                except asyncio.TimeoutError:
                    continue

        for fake in fake_vllm:
            tasks.append(asyncio.create_task(fake.run(stop), name=f"fake-vllm-{fake.index}"))
        if fake_vllm:
            # give fake servers a moment to bind before the first probe/scrape
            await asyncio.sleep(0.15)
        try:
            await asyncio.to_thread(svc_registry.refresh)
        except Exception as exc:
            log.warning("initial service registry refresh failed: %s", exc)
        tasks.append(asyncio.create_task(refresh_loop(), name="service-refresh"))
        tasks.append(
            asyncio.create_task(proxy.run(stop, float(config.scrape_interval_seconds)), name="vllm-proxy")
        )
        if pusher is not None and cursors is not None:
            loki_client = httpx.AsyncClient()
            manager = LogManager(svc_registry, pusher, cursors)
            tasks.append(asyncio.create_task(cursors.run(stop), name="cursors"))
            tasks.append(asyncio.create_task(pusher.run(stop, loki_client), name="loki-pusher"))
            tasks.append(asyncio.create_task(manager.run(stop), name="log-manager"))
        tasks.append(asyncio.create_task(heartbeat.run(stop), name="heartbeat"))
        app.state.heartbeat_stop = stop
        app.state.heartbeat_task = tasks[-1]
        try:
            yield
        finally:
            stop.set()
            for task in tasks:
                task.cancel()
            for task in tasks:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            if loki_client is not None:
                await loki_client.aclose()

    app = FastAPI(title="ai-monitor-agent", lifespan=lifespan)
    app.state.config = config
    app.state.host = host
    app.state.registry = prom_registry
    app.state.services = svc_registry
    app.state.heartbeat = heartbeat
    app.state.proxy = proxy

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(content=render(prom_registry) + proxy.render(), media_type=CONTENT_TYPE_LATEST)

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
        return {
            "status": "ok",
            "host": host,
            "collectors": collectors,
            "services": len(svc_registry.snapshot()),
            "logs": logs_enabled,
        }

    return app
