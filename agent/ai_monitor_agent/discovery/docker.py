"""Discover vLLM services running as Docker containers."""

from __future__ import annotations

import logging
import shlex
from datetime import datetime, timezone
from typing import Any, Callable

import docker

from ..services import ServiceInfo
from .process import port_from_cmdline
from .process_info import redact_env

log = logging.getLogger(__name__)

DEFAULT_VLLM_PORT = 8000


def _parse_rfc3339(value: str | None) -> float | None:
    """Docker's ``StartedAt`` is RFC3339 with nanoseconds; trim to microseconds."""
    if not value or value.startswith("0001-01-01"):
        return None
    try:
        text = value.rstrip("Z")
        if "." in text:
            head, frac = text.split(".", 1)
            # frac may carry a timezone offset after the digits
            digits = ""
            rest = ""
            for i, ch in enumerate(frac):
                if ch.isdigit():
                    digits += ch
                else:
                    rest = frac[i:]
                    break
            text = f"{head}.{digits[:6].ljust(6, '0')}{rest}"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _env_list_to_dict(items: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items or []:
        key, sep, val = item.partition("=")
        if sep:
            out[key] = val
    return out


def _host_port(ports: dict[str, Any] | None, container_port: int) -> int | None:
    if not ports:
        return None
    for proto in ("tcp", ""):
        key = f"{container_port}/{proto}" if proto else str(container_port)
        bindings = ports.get(key)
        if not bindings:
            continue
        for b in bindings:
            try:
                return int(b.get("HostPort"))
            except (TypeError, ValueError):
                continue
    return None


def _container_ip(settings: dict[str, Any]) -> str | None:
    for net in (settings.get("Networks") or {}).values():
        ip = (net or {}).get("IPAddress")
        if ip:
            return ip
    return settings.get("IPAddress") or None


def container_to_service(attrs: dict[str, Any]) -> ServiceInfo | None:
    """Map ``docker inspect`` attrs to a ``ServiceInfo``; ``None`` if not a running vLLM."""
    state = attrs.get("State") or {}
    if state.get("Status", "running") != "running":
        return None

    config = attrs.get("Config") or {}
    entrypoint = list(config.get("Entrypoint") or [])
    cmd = list(config.get("Cmd") or [])
    args = list(attrs.get("Args") or [])
    image = str(config.get("Image") or "")

    full_cmd = entrypoint + cmd if (entrypoint or cmd) else [attrs.get("Path") or ""] + args
    full_cmd = [str(x) for x in full_cmd if x is not None]
    haystack = " ".join(full_cmd + args).lower()
    if "vllm" not in haystack and "vllm" not in image.lower():
        return None

    env = _env_list_to_dict(config.get("Env"))
    port = port_from_cmdline(full_cmd + args)
    if port is None:
        try:
            port = int(env["VLLM_PORT"]) if env.get("VLLM_PORT") else None
        except ValueError:
            port = None
    container_port = port or DEFAULT_VLLM_PORT

    settings = attrs.get("NetworkSettings") or {}
    network_mode = str((attrs.get("HostConfig") or {}).get("NetworkMode") or "")
    if network_mode == "host":
        public_port = container_port
        metrics_url = f"http://127.0.0.1:{container_port}"
    else:
        mapped = _host_port(settings.get("Ports"), container_port)
        if mapped is not None:
            public_port = mapped
            metrics_url = f"http://127.0.0.1:{mapped}"
        else:
            ip = _container_ip(settings) or "127.0.0.1"
            public_port = container_port
            metrics_url = f"http://{ip}:{container_port}"

    name = str(attrs.get("Name") or "").lstrip("/") or str(attrs.get("Id") or "")[:12]
    pid = state.get("Pid") or None

    return ServiceInfo(
        name=name,
        source="docker",
        port=public_port,
        metrics_url=metrics_url,
        pid=int(pid) if pid else None,
        container_id=attrs.get("Id"),
        container_name=name,
        log_source="docker",
        started_at=_parse_rfc3339(state.get("StartedAt")),
        cmdline=shlex.join(full_cmd) if full_cmd else None,
        env=redact_env(env),
    )


class DockerDiscovery:
    name = "docker"

    def __init__(self, client_factory: Callable[[], Any] = docker.from_env) -> None:
        self._factory = client_factory
        self._client: Any | None = None
        self._disabled = False
        self.last_pids: set[int] = set()

    def available(self) -> bool:
        """Connect and ping once; on failure warn once and disable permanently."""
        if self._disabled:
            return False
        if self._client is not None:
            return True
        try:
            client = self._factory()
            client.ping()
        except Exception as exc:
            log.warning("docker discovery disabled: %s", exc)
            self._disabled = True
            return False
        self._client = client
        return True

    def discover(self) -> list[ServiceInfo]:
        if not self.available():
            return []
        assert self._client is not None
        try:
            containers = self._client.containers.list()
        except Exception as exc:
            log.warning("docker container listing failed: %s", exc)
            return []
        services: list[ServiceInfo] = []
        pids: set[int] = set()
        for container in containers:
            try:
                svc = container_to_service(container.attrs)
            except Exception as exc:
                log.debug("skipping container: %s", exc)
                continue
            if svc is None:
                continue
            services.append(svc)
            if svc.pid:
                pids.add(svc.pid)
        self.last_pids = pids
        return services
