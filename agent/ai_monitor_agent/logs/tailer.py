"""Tail service logs from files or docker containers into the LokiPusher."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable

from ..services import ServiceInfo
from .cursors import CursorStore
from .level_parser import parse_level
from .loki_client import LogLine, LokiPusher

log = logging.getLogger(__name__)

_DOCKER_TS_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})\s?"
)


def parse_docker_line(raw: str) -> tuple[int, str]:
    """Split ``<RFC3339Nano> <message>``; falls back to now when no timestamp."""
    m = _DOCKER_TS_RE.match(raw)
    if not m:
        return time.time_ns(), raw
    base, frac, tz = m.groups()
    try:
        dt = datetime.fromisoformat(base + ("+00:00" if tz == "Z" else tz))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        seconds = int(dt.timestamp())
        nanos = int((frac or "0").ljust(9, "0")[:9])
        return seconds * 10**9 + nanos, raw[m.end() :]
    except ValueError:
        return time.time_ns(), raw


class FileTailer:
    """Follow a log file by offset; detects rotation (inode change) and truncation."""

    def __init__(self, service: str, path: str, pusher: LokiPusher, cursors: CursorStore) -> None:
        self.service = service
        self.path = path
        self.pusher = pusher
        self.cursors = cursors
        self.key = f"file:{path}"
        self._prev_level = "unknown"

    def _read_once(self, inode: int | None, offset: int) -> tuple[int | None, int]:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return inode, offset
        if inode != st.st_ino or st.st_size < offset:
            inode, offset = st.st_ino, 0
        if st.st_size == offset:
            return inode, offset
        with open(self.path, "rb") as fh:
            fh.seek(offset)
            data = fh.read()
        if not data:
            return inode, offset
        end = data.rfind(b"\n")
        if end < 0:
            return inode, offset  # only a partial line so far; retry later
        complete = data[: end + 1]
        now = time.time_ns()
        for raw in complete.split(b"\n")[:-1]:
            text = raw.decode("utf-8", errors="replace").rstrip("\r")
            level = parse_level(text, self._prev_level)
            self._prev_level = level
            self.pusher.enqueue(self.service, "file", LogLine(ts_ns=now, line=text, level=level))
            now += 1
        offset += len(complete)
        self.cursors.set(self.key, {"inode": inode, "offset": offset})
        return inode, offset

    async def run(self, stop: asyncio.Event, poll_interval: float = 0.5) -> None:
        cur = self.cursors.get(self.key) or {}
        inode: int | None = cur.get("inode")
        offset: int = int(cur.get("offset") or 0)
        while not stop.is_set():
            try:
                inode, offset = self._read_once(inode, offset)
            except Exception as exc:
                log.warning("tail %s failed: %s", self.path, exc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_interval)
            except asyncio.TimeoutError:
                continue


class DockerTailer:
    """Follow ``docker logs`` for one container; the blocking stream runs in a thread."""

    def __init__(self, service: str, container: Any, pusher: LokiPusher, cursors: CursorStore) -> None:
        self.service = service
        self.container = container
        self.pusher = pusher
        self.cursors = cursors
        self.key = f"docker:{container.id}"
        self._prev_level = "unknown"
        cur = self.cursors.get(self.key) or {}
        self._last_ts_ns: int = int(cur.get("ts_ns") or 0)
        self._since: int = int(cur.get("since") or 0)

    def _handle_line(self, raw: str) -> None:
        ts_ns, text = parse_docker_line(raw.rstrip("\r"))
        if ts_ns <= self._last_ts_ns:
            return  # already pushed before restart
        level = parse_level(text, self._prev_level)
        self._prev_level = level
        self.pusher.enqueue(self.service, "docker", LogLine(ts_ns=ts_ns, line=text, level=level))
        self._last_ts_ns = ts_ns
        self._since = ts_ns // 10**9
        self.cursors.set(self.key, {"since": self._since, "ts_ns": ts_ns})

    def _stream(self, stop: asyncio.Event, loop: asyncio.AbstractEventLoop) -> None:
        """Runs in a worker thread; forwards complete lines to the event loop."""
        buf = b""
        for chunk in self.container.logs(stream=True, follow=True, since=int(self._since), timestamps=True):
            if stop.is_set():
                break
            if not chunk:
                continue
            buf += chunk if isinstance(chunk, bytes) else str(chunk).encode()
            while True:
                idx = buf.find(b"\n")
                if idx < 0:
                    break
                line, buf = buf[:idx], buf[idx + 1 :]
                loop.call_soon_threadsafe(self._handle_line, line.decode("utf-8", errors="replace"))
        if buf and not stop.is_set():
            loop.call_soon_threadsafe(self._handle_line, buf.decode("utf-8", errors="replace"))

    async def run(self, stop: asyncio.Event, reconnect_delay: float = 2.0) -> None:
        loop = asyncio.get_running_loop()
        while not stop.is_set():
            try:
                await asyncio.to_thread(self._stream, stop, loop)
            except Exception as exc:
                log.warning("docker log stream for %s failed: %s", self.service, exc)
            if stop.is_set():
                break
            try:
                await asyncio.wait_for(stop.wait(), timeout=reconnect_delay)
            except asyncio.TimeoutError:
                continue


class LogManager:
    """Keep one tailer task per service whose ``log_source != "none"``."""

    def __init__(
        self,
        registry: Any,
        pusher: LokiPusher,
        cursors: CursorStore,
        docker_client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.registry = registry
        self.pusher = pusher
        self.cursors = cursors
        self._docker_factory = docker_client_factory
        self._docker_client: Any | None = None
        self._tasks: dict[str, tuple[tuple, asyncio.Task, asyncio.Event]] = {}

    def active(self) -> list[str]:
        return list(self._tasks)

    @staticmethod
    def _identity(svc: ServiceInfo) -> tuple | None:
        if svc.log_source == "file" and svc.log_path:
            return ("file", svc.log_path)
        if svc.log_source == "docker" and svc.container_id:
            return ("docker", svc.container_id)
        return None

    def _docker(self) -> Any | None:
        if self._docker_client is None and self._docker_factory is not None:
            try:
                self._docker_client = self._docker_factory()
            except Exception as exc:
                log.warning("docker log client unavailable: %s", exc)
        return self._docker_client

    def _make_tailer(self, svc: ServiceInfo, identity: tuple) -> FileTailer | DockerTailer | None:
        kind, ref = identity
        if kind == "file":
            return FileTailer(svc.name, ref, self.pusher, self.cursors)
        client = self._docker()
        if client is None:
            return None
        try:
            container = client.containers.get(ref)
        except Exception as exc:
            log.warning("cannot open container %s for logs: %s", ref, exc)
            return None
        return DockerTailer(svc.name, container, self.pusher, self.cursors)

    async def _stop_task(self, name: str) -> None:
        _identity, task, stop = self._tasks.pop(name)
        stop.set()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    async def reconcile(self) -> None:
        wanted: dict[str, tuple[ServiceInfo, tuple]] = {}
        for svc in self.registry.snapshot():
            identity = self._identity(svc)
            if identity is not None:
                wanted[svc.name] = (svc, identity)

        for name in list(self._tasks):
            identity, task, _stop = self._tasks[name]
            if name not in wanted or wanted[name][1] != identity or task.done():
                await self._stop_task(name)

        for name, (svc, identity) in wanted.items():
            if name in self._tasks:
                continue
            tailer = self._make_tailer(svc, identity)
            if tailer is None:
                continue
            stop = asyncio.Event()
            task = asyncio.create_task(tailer.run(stop), name=f"tail:{name}")
            self._tasks[name] = (identity, task, stop)
            log.info("tailing logs for %s (%s)", name, identity[0])

    async def run(self, stop: asyncio.Event, interval: float = 5.0) -> None:
        try:
            while not stop.is_set():
                try:
                    await self.reconcile()
                except Exception as exc:
                    log.warning("log manager reconcile failed: %s", exc)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    continue
        finally:
            for name in list(self._tasks):
                await self._stop_task(name)
