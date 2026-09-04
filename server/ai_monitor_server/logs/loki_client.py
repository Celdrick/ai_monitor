from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx


class LokiError(Exception):
    """Raised when Loki is unreachable or returns an error."""


def ns_to_iso(ns: int) -> str:
    """Nanoseconds since epoch -> ISO 8601 / RFC 3339 string (microsecond precision)."""
    seconds, rem = divmod(int(ns), 1_000_000_000)
    dt = datetime.fromtimestamp(seconds, tz=timezone.utc) + timedelta(microseconds=rem // 1000)
    return dt.isoformat()


def parse_streams(streams: Any) -> list[dict[str, str]]:
    """Flatten Loki ``streams`` (query_range ``data.result`` or a tail frame's
    ``streams``) into ``[{"ts","level","line"}]`` sorted by timestamp ascending.

    ``level`` comes from the stream labels (``unknown`` if absent).
    """
    rows: list[tuple[int, str, str]] = []
    if not isinstance(streams, list):
        return []
    for stream in streams:
        if not isinstance(stream, dict):
            continue
        labels = stream.get("stream") or {}
        level = str(labels.get("level") or "unknown")
        for value in stream.get("values") or []:
            try:
                ts_ns = int(value[0])
                line = str(value[1])
            except (TypeError, ValueError, IndexError):
                continue
            rows.append((ts_ns, level, line))
    rows.sort(key=lambda r: r[0])
    return [{"ts": ns_to_iso(ts), "level": level, "line": line} for ts, level, line in rows]


class LokiClient:
    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def query_range(
        self, query: str, start_ns: int, end_ns: int, limit: int, direction: str
    ) -> list[dict[str, str]]:
        params = {
            "query": query,
            "start": str(int(start_ns)),
            "end": str(int(end_ns)),
            "limit": str(int(limit)),
            "direction": direction,
        }
        try:
            resp = await self._client.get("/loki/api/v1/query_range", params=params)
        except httpx.HTTPError as exc:
            raise LokiError(f"Loki unreachable: {exc}") from exc
        if resp.status_code < 200 or resp.status_code >= 300:
            raise LokiError(f"Loki returned HTTP {resp.status_code}: {resp.text[:500]}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise LokiError("Loki returned non-JSON response") from exc
        if not isinstance(body, dict) or body.get("status") != "success":
            raise LokiError(f"Loki query failed: {body}")
        data = body.get("data") or {}
        return parse_streams(data.get("result"))

    def tail_url(self, query: str, start_ns: int, limit: int = 100) -> str:
        """WebSocket URL for Loki's live tail endpoint (http(s) -> ws(s))."""
        if self.base_url.startswith("https://"):
            ws_base = "wss://" + self.base_url[len("https://") :]
        elif self.base_url.startswith("http://"):
            ws_base = "ws://" + self.base_url[len("http://") :]
        else:
            ws_base = self.base_url
        qs = urlencode(
            {"query": query, "delay_for": "0", "limit": str(int(limit)), "start": str(int(start_ns))}
        )
        return f"{ws_base}/loki/api/v1/tail?{qs}"
