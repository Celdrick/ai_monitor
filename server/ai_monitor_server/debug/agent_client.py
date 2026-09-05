"""HTTP client for an agent's /control API."""

from __future__ import annotations

from typing import Any

import httpx


def agent_base(advertise_address: str) -> str:
    addr = advertise_address.strip()
    if addr.startswith("http://") or addr.startswith("https://"):
        return addr.rstrip("/")
    return f"http://{addr}"


class AgentControlError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def control_request(
    advertise: str,
    token: str,
    method: str,
    path: str,
    json: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> httpx.Response:
    url = agent_base(advertise) + path
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.request(method, url, json=json, headers=headers, timeout=timeout)
        except httpx.HTTPError as exc:
            raise AgentControlError(502, f"agent unreachable: {exc}") from exc
    return resp
