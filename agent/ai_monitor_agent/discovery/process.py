"""Discover vLLM services running as plain host processes (nohup / tmux)."""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable

import psutil

from ..services import ServiceInfo

log = logging.getLogger(__name__)

DEFAULT_VLLM_PORT = 8000
_SERVE_MARKERS = ("serve", "api_server", "entrypoints.openai")


def is_vllm_cmdline(cmdline: list[str]) -> bool:
    if not cmdline:
        return False
    joined = " ".join(cmdline)
    return "vllm" in joined and any(m in joined for m in _SERVE_MARKERS)


def port_from_cmdline(cmdline: list[str]) -> int | None:
    """``--port 8001`` or ``--port=8001``; ``None`` when absent or malformed."""
    for i, arg in enumerate(cmdline):
        value: str | None = None
        if arg == "--port":
            if i + 1 < len(cmdline):
                value = cmdline[i + 1]
        elif arg.startswith("--port="):
            value = arg.split("=", 1)[1]
        if value is None:
            continue
        try:
            return int(value)
        except ValueError:
            return None
    return None


class ProcessDiscovery:
    name = "process"

    def __init__(
        self,
        proc_iter: Callable[..., Iterable[Any]] = psutil.process_iter,
        net_connections: Callable[..., Iterable[Any]] = psutil.net_connections,
        exclude_pids: Callable[[], set[int]] = set,
    ) -> None:
        self._proc_iter = proc_iter
        self._net_connections = net_connections
        self._exclude_pids = exclude_pids

    def _listen_ports(self) -> dict[int, list[int]]:
        ports: dict[int, list[int]] = {}
        try:
            conns = self._net_connections(kind="inet")
        except Exception as exc:  # psutil.AccessDenied on some systems
            log.debug("net_connections failed: %s", exc)
            return ports
        for c in conns:
            if getattr(c, "status", None) != psutil.CONN_LISTEN or c.pid is None:
                continue
            laddr = getattr(c, "laddr", None)
            port = getattr(laddr, "port", None)
            if port:
                ports.setdefault(c.pid, []).append(int(port))
        return ports

    def discover(self) -> list[ServiceInfo]:
        try:
            excluded = set(self._exclude_pids())
        except Exception:
            excluded = set()

        parents: dict[int, int] = {}
        matched: dict[int, list[str]] = {}
        for proc in self._proc_iter(attrs=["pid", "ppid", "cmdline"]):
            try:
                info = proc.info
                pid = int(info["pid"])
                ppid = info.get("ppid")
                cmdline = list(info.get("cmdline") or [])
            except Exception:
                continue
            parents[pid] = int(ppid) if ppid is not None else 0
            if is_vllm_cmdline(cmdline):
                matched[pid] = cmdline

        def ancestors(pid: int) -> Iterable[int]:
            cur = parents.get(pid, 0)
            seen = {pid}
            while cur and cur not in seen:
                yield cur
                seen.add(cur)
                cur = parents.get(cur, 0)

        # keep only the top-most matched process in each tree, and drop any
        # process that is (or descends from) an excluded pid, e.g. a container
        top: dict[int, list[str]] = {}
        for pid, cmdline in matched.items():
            if pid in excluded:
                continue
            chain = list(ancestors(pid))
            if any(a in excluded for a in chain) or any(a in matched for a in chain):
                continue
            top[pid] = cmdline

        listen = self._listen_ports() if top else {}
        services: list[ServiceInfo] = []
        for pid, cmdline in top.items():
            port = port_from_cmdline(cmdline)
            if port is None and listen.get(pid):
                port = min(listen[pid])
            if port is None:
                port = DEFAULT_VLLM_PORT
            services.append(
                ServiceInfo(
                    name=f"vllm-{port}",
                    source="process",
                    port=port,
                    metrics_url=f"http://127.0.0.1:{port}",
                    pid=pid,
                    log_source="none",
                )
            )
        return services
