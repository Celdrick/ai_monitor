"""Scrape each discovered vLLM's /metrics and re-expose it with host/service labels."""

from __future__ import annotations

import asyncio
import logging
import threading

import httpx
from prometheus_client.parser import text_string_to_metric_families
from prometheus_client.utils import floatToGoString

from ..discovery.registry import ServiceRegistry

log = logging.getLogger(__name__)

# family types whose sample names carry a suffix that the parser strips
_TYPE_SUFFIX = {"counter": "_total"}


def _escape_label(value: str) -> str:
    return value.replace("\\", r"\\").replace("\n", r"\n").replace('"', r"\"")


def _escape_help(value: str) -> str:
    return value.replace("\\", r"\\").replace("\n", r"\n")


def relabel_vllm_text(text: str, host: str, service: str) -> str:
    """Keep only ``vllm*`` families, add ``host``/``service`` labels, re-serialize (0.0.4 text).

    Malformed input yields ``""`` rather than raising so one bad service
    cannot break the agent's own metrics endpoint.
    """
    try:
        families = list(text_string_to_metric_families(text))
    except Exception as exc:
        log.warning("cannot parse vLLM metrics for %s: %s", service, exc)
        return ""

    out: list[str] = []
    for fam in families:
        if not fam.name.startswith("vllm"):
            continue
        ftype = fam.type or "unknown"
        header_name = fam.name + _TYPE_SUFFIX.get(ftype, "")
        if fam.documentation:
            out.append(f"# HELP {header_name} {_escape_help(fam.documentation)}")
        out.append(f"# TYPE {header_name} {'untyped' if ftype == 'unknown' else ftype}")
        for s in fam.samples:
            labels = {"host": host, "service": service}
            labels.update({k: v for k, v in s.labels.items() if k not in ("host", "service")})
            label_str = ",".join(f'{k}="{_escape_label(str(v))}"' for k, v in labels.items())
            line = f"{s.name}{{{label_str}}} {floatToGoString(s.value)}"
            if s.timestamp is not None:
                line += f" {int(float(s.timestamp) * 1000)}"
            out.append(line)
    if not out:
        return ""
    return "\n".join(out) + "\n"


class VllmMetricsProxy:
    def __init__(self, registry: ServiceRegistry, host: str, timeout: float = 5.0) -> None:
        self.registry = registry
        self.host = host
        self.timeout = timeout
        self._lock = threading.Lock()
        self._cache: dict[str, str] = {}
        self._status: dict[str, bool] = {}

    async def _scrape(self, client: httpx.AsyncClient, name: str, metrics_url: str) -> tuple[str, str | None]:
        url = metrics_url.rstrip("/") + "/metrics"
        try:
            resp = await client.get(url, timeout=self.timeout)
        except Exception as exc:
            log.debug("scrape %s (%s) failed: %s", name, url, exc)
            return name, None
        if resp.status_code != 200:
            log.debug("scrape %s (%s) returned %s", name, url, resp.status_code)
            return name, None
        return name, relabel_vllm_text(resp.text, self.host, name)

    async def refresh_once(self, client: httpx.AsyncClient) -> None:
        services = self.registry.snapshot()
        results = await asyncio.gather(
            *(self._scrape(client, s.name, s.metrics_url) for s in services),
            return_exceptions=True,
        )
        cache: dict[str, str] = {}
        status: dict[str, bool] = {}
        for svc, res in zip(services, results):
            if isinstance(res, BaseException):
                log.debug("scrape %s raised: %s", svc.name, res)
                ok, text = False, None
            else:
                _name, text = res
                ok = text is not None
            status[svc.name] = ok
            if ok and text:
                cache[svc.name] = text
            self.registry.set_scrape_ok(svc.name, ok)
        with self._lock:
            self._cache = cache
            self._status = status

    async def run(self, stop: asyncio.Event, interval: float) -> None:
        async with httpx.AsyncClient() as client:
            while not stop.is_set():
                try:
                    await self.refresh_once(client)
                except Exception as exc:  # never let the loop die
                    log.warning("vLLM metrics refresh failed: %s", exc)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    continue

    def render(self) -> bytes:
        with self._lock:
            parts = [self._cache[k] for k in sorted(self._cache)]
        return "".join(parts).encode("utf-8")

    def scrape_status(self) -> dict[str, bool]:
        with self._lock:
            return dict(self._status)
