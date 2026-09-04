"""Periodic heartbeat/registration to the AI Monitor server."""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import Any, Callable

import httpx

from . import __version__
from .config import AgentConfig

log = logging.getLogger(__name__)

HEARTBEAT_PATH = "/api/agents/heartbeat"


def detect_ip() -> str:
    """Best-effort primary IP detection via a UDP connect (no packets are sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


class HeartbeatClient:
    def __init__(
        self,
        config: AgentConfig,
        host: str,
        inventory: Callable[[], dict[str, Any]],
        timeout: float = 10.0,
    ) -> None:
        self.config = config
        self.host = host
        self.inventory = inventory
        self.interval: float = float(config.heartbeat_interval_seconds)
        self.timeout = timeout
        self.url = config.server_url.rstrip("/") + HEARTBEAT_PATH

    def build_payload(self) -> dict[str, Any]:
        inv = self.inventory()
        listen_port = self.config.listen_port
        ip = detect_ip()
        advertise = self.config.advertise_address or f"{ip}:{listen_port}"
        return {
            "host": self.host,
            "ip": ip,
            "listen_port": listen_port,
            "advertise_address": advertise,
            "agent_version": __version__,
            "hardware_vendor": inv.get("hardware_vendor", "none"),
            "device_count": int(inv.get("device_count", 0)),
            "services": list(inv.get("services", [])),
        }

    async def send_once(self, client: httpx.AsyncClient) -> bool:
        """Send a single heartbeat. Never raises; returns True on 2xx."""
        try:
            payload = self.build_payload()
            resp = await client.post(
                self.url,
                json=payload,
                headers={"Authorization": f"Bearer {self.config.agent_token}"},
                timeout=self.timeout,
            )
            if 200 <= resp.status_code < 300:
                log.debug("heartbeat ok (%s)", resp.status_code)
                return True
            log.warning("heartbeat rejected by %s: %s %s", self.url, resp.status_code, resp.text[:200])
            return False
        except Exception as exc:
            log.warning("heartbeat to %s failed: %s", self.url, exc)
            return False

    async def run(self, stop: asyncio.Event) -> None:
        """Send heartbeats every `interval` seconds until `stop` is set."""
        async with httpx.AsyncClient() as client:
            while not stop.is_set():
                await self.send_once(client)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.interval)
                except asyncio.TimeoutError:
                    continue
