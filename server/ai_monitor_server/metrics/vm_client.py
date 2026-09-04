from typing import Any

import httpx


class VMError(Exception):
    """Raised when VictoriaMetrics is unreachable or returns an error."""


def _ts(value: float | int) -> str:
    """Format a unix timestamp; integral floats become ints (1700000000 not 1700000000.0)."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


class VMClient:
    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        try:
            resp = await self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise VMError(f"VictoriaMetrics unreachable: {exc}") from exc
        if resp.status_code < 200 or resp.status_code >= 300:
            raise VMError(
                f"VictoriaMetrics returned HTTP {resp.status_code}: {resp.text[:500]}"
            )
        try:
            body = resp.json()
        except ValueError as exc:
            raise VMError("VictoriaMetrics returned non-JSON response") from exc
        if not isinstance(body, dict) or body.get("status") != "success":
            detail = body.get("error") if isinstance(body, dict) else None
            raise VMError(f"VictoriaMetrics query failed: {detail or body}")
        return body.get("data")

    async def query(self, expr: str, time: float | None = None) -> Any:
        params: dict[str, Any] = {"query": expr}
        if time is not None:
            params["time"] = _ts(time)
        return await self._get("/api/v1/query", params)

    async def query_range(self, expr: str, start: float, end: float, step: str) -> Any:
        return await self._get(
            "/api/v1/query_range",
            {"query": expr, "start": _ts(start), "end": _ts(end), "step": step},
        )
