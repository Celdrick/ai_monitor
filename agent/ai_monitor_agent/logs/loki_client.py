"""Batching, buffering, backing-off pusher for Loki's HTTP push API."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

PUSH_PATH = "/loki/api/v1/push"
BACKOFF_MIN = 1.0
BACKOFF_MAX = 30.0


@dataclass
class LogLine:
    ts_ns: int
    line: str
    level: str


Entry = tuple[str, str, LogLine]  # (service, source, line)


class LokiPusher:
    def __init__(
        self,
        loki_url: str,
        host: str,
        batch_lines: int,
        batch_interval: float,
        buffer_max_lines: int,
    ) -> None:
        self.url = loki_url.rstrip("/") + PUSH_PATH
        self.host = host
        self.batch_lines = max(1, int(batch_lines))
        self.batch_interval = float(batch_interval)
        self.buffer_max_lines = max(1, int(buffer_max_lines))
        self.dropped: dict[str, int] = {}
        self.backoff: float = 0.0
        self.next_attempt: float = 0.0
        self._buffer: deque[Entry] = deque()
        self._wake = asyncio.Event()

    # -- buffer --------------------------------------------------------------

    def pending(self) -> int:
        return len(self._buffer)

    def _trim(self) -> None:
        while len(self._buffer) > self.buffer_max_lines:
            service, _src, _line = self._buffer.popleft()
            self.dropped[service] = self.dropped.get(service, 0) + 1

    def enqueue(self, service: str, source: str, line: LogLine) -> None:
        self._buffer.append((service, source, line))
        self._trim()
        if len(self._buffer) >= self.batch_lines:
            self._wake.set()

    # -- payload -------------------------------------------------------------

    def build_payload(self, batch: list[Entry]) -> dict[str, Any]:
        streams: dict[tuple[str, str, str], list[list[str]]] = {}
        last_ts: dict[tuple[str, str, str], int] = {}
        for service, source, line in batch:
            key = (service, source, line.level)
            ts = int(line.ts_ns)
            prev = last_ts.get(key)
            if prev is not None and ts <= prev:
                ts = prev + 1
            last_ts[key] = ts
            streams.setdefault(key, []).append([str(ts), line.line])
        return {
            "streams": [
                {
                    "stream": {"host": self.host, "service": service, "source": source, "level": level},
                    "values": values,
                }
                for (service, source, level), values in streams.items()
            ]
        }

    # -- sending -------------------------------------------------------------

    async def flush(self, client: httpx.AsyncClient) -> bool:
        """Send one batch. Returns True when nothing is pending or the push succeeded."""
        if not self._buffer:
            return True
        batch: list[Entry] = []
        while self._buffer and len(batch) < self.batch_lines:
            batch.append(self._buffer.popleft())
        try:
            resp = await client.post(self.url, json=self.build_payload(batch), timeout=10.0)
            ok = 200 <= resp.status_code < 300
            if not ok:
                log.warning("loki push rejected: %s %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            ok = False
            log.warning("loki push to %s failed: %s", self.url, exc)

        if ok:
            self.backoff = 0.0
            self.next_attempt = 0.0
            return True

        # put the batch back in front, oldest first, then enforce the cap
        self._buffer.extendleft(reversed(batch))
        self._trim()
        self.backoff = BACKOFF_MIN if self.backoff <= 0 else min(self.backoff * 2, BACKOFF_MAX)
        self.next_attempt = time.monotonic() + self.backoff
        return False

    async def run(self, stop: asyncio.Event, client: httpx.AsyncClient) -> None:
        while not stop.is_set():
            now = time.monotonic()
            timeout = self.batch_interval
            if self.next_attempt > now:
                timeout = max(timeout, self.next_attempt - now)
            stop_task = asyncio.ensure_future(stop.wait())
            wake_task = asyncio.ensure_future(self._wake.wait())
            try:
                await asyncio.wait({stop_task, wake_task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for t in (stop_task, wake_task):
                    if not t.done():
                        t.cancel()
            self._wake.clear()
            if stop.is_set():
                break
            if self._buffer and time.monotonic() >= self.next_attempt:
                await self.flush(client)
        # best-effort final flush; do not wait out the backoff on shutdown
        if self._buffer:
            await self.flush(client)
