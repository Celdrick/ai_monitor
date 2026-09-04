"""Merge discovery sources into one deduplicated, enriched service list."""

from __future__ import annotations

import copy
import logging
import threading
from typing import Any, Callable, Protocol

from ..services import ServiceInfo
from ..vllm.probe import fetch_model_and_version
from .process_info import ProcessInfo, read_process_info

log = logging.getLogger(__name__)

_PRIORITY = {"manual": 0, "docker": 1, "process": 2}
_ENRICH_FIELDS = (
    "pid",
    "container_id",
    "container_name",
    "log_path",
    "profiler_dir",
    "model",
    "vllm_version",
    "started_at",
    "cmdline",
    "cwd",
)


class Source(Protocol):
    name: str

    def discover(self) -> list[ServiceInfo]: ...


def _merge_group(group: list[ServiceInfo]) -> ServiceInfo:
    """Highest-priority entry wins; lower ones fill in what it lacks."""
    group = sorted(group, key=lambda s: _PRIORITY.get(s.source, 99))
    top = copy.deepcopy(group[0])
    for other in group[1:]:
        for name in _ENRICH_FIELDS:
            if getattr(top, name) is None and getattr(other, name) is not None:
                setattr(top, name, getattr(other, name))
        if not top.env and other.env:
            top.env = dict(other.env)
        if top.log_source == "none" and other.log_source != "none":
            top.log_source = other.log_source
        # a manual entry only guesses 127.0.0.1:<port>; discovery knows better
        if top.source == "manual" and other.source == "docker":
            top.metrics_url = other.metrics_url
    return top


class ServiceRegistry:
    def __init__(
        self,
        sources: list[Any],
        probe: Callable[[str], tuple[str | None, str | None]] = fetch_model_and_version,
        process_info: Callable[[int], ProcessInfo] = read_process_info,
    ) -> None:
        self._sources = list(sources)
        self._probe = probe
        self._process_info = process_info
        self._lock = threading.Lock()
        self._services: dict[str, ServiceInfo] = {}

    # -- discovery -----------------------------------------------------------

    def _collect(self) -> list[ServiceInfo]:
        found: list[ServiceInfo] = []
        for src in self._sources:
            try:
                found.extend(src.discover())
            except Exception as exc:
                log.warning("discovery source %s failed: %s", getattr(src, "name", src), exc)
        return found

    @staticmethod
    def _dedupe(found: list[ServiceInfo]) -> list[ServiceInfo]:
        # drop process entries whose pid is already claimed by a docker container
        docker_pids = {s.pid for s in found if s.source == "docker" and s.pid}
        found = [s for s in found if not (s.source == "process" and s.pid in docker_pids)]
        by_port: dict[int, list[ServiceInfo]] = {}
        for s in found:
            by_port.setdefault(s.port, []).append(s)
        merged = [_merge_group(g) for g in by_port.values()]
        # names must be unique per host; later duplicates get a port suffix
        seen: set[str] = set()
        for s in merged:
            if s.name in seen:
                s.name = f"{s.name}-{s.port}"
            seen.add(s.name)
        return merged

    def _enrich(self, svc: ServiceInfo, prev: ServiceInfo | None) -> None:
        if prev is not None:
            svc.scrape_ok = prev.scrape_ok
            if svc.model is None:
                svc.model = prev.model
            if svc.vllm_version is None:
                svc.vllm_version = prev.vllm_version
            if prev.pid is not None and prev.pid == svc.pid:
                for name in ("cmdline", "cwd", "started_at"):
                    if getattr(svc, name) is None:
                        setattr(svc, name, getattr(prev, name))
                if not svc.env:
                    svc.env = dict(prev.env)

        needs_pinfo = svc.pid is not None and (
            svc.cmdline is None or svc.cwd is None or svc.started_at is None or not svc.env
        )
        if needs_pinfo and not (prev is not None and prev.pid == svc.pid):
            try:
                info = self._process_info(svc.pid)  # type: ignore[arg-type]
            except Exception as exc:
                log.debug("process info for pid %s failed: %s", svc.pid, exc)
                info = ProcessInfo(None, None, None, {})
            if svc.cmdline is None:
                svc.cmdline = info.cmdline
            if svc.cwd is None:
                svc.cwd = info.cwd
            if svc.started_at is None:
                svc.started_at = info.started_at
            if not svc.env:
                svc.env = dict(info.env)

        if svc.model is None or svc.vllm_version is None:
            try:
                model, version = self._probe(svc.metrics_url)
            except Exception as exc:
                log.debug("probe %s failed: %s", svc.metrics_url, exc)
                model, version = None, None
            if svc.model is None:
                svc.model = model
            if svc.vllm_version is None:
                svc.vllm_version = version

    def refresh(self) -> list[ServiceInfo]:
        """Run all sources, merge, enrich; blocking (call via ``asyncio.to_thread``)."""
        merged = self._dedupe(self._collect())
        with self._lock:
            previous = dict(self._services)
        for svc in merged:
            self._enrich(svc, previous.get(svc.name))
        with self._lock:
            # keep scrape flags set concurrently by the proxy during this refresh
            for svc in merged:
                cur = self._services.get(svc.name)
                if cur is not None:
                    svc.scrape_ok = cur.scrape_ok
            self._services = {s.name: s for s in merged}
            return [copy.deepcopy(s) for s in merged]

    # -- read side -----------------------------------------------------------

    def snapshot(self) -> list[ServiceInfo]:
        with self._lock:
            return [copy.deepcopy(s) for s in self._services.values()]

    def set_scrape_ok(self, name: str, ok: bool) -> None:
        with self._lock:
            svc = self._services.get(name)
            if svc is not None:
                svc.scrape_ok = bool(ok)
