"""Fetch model id and vLLM version from a service's OpenAI-compatible API."""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)


def fetch_model_and_version(base_url: str, timeout: float = 3.0) -> tuple[str | None, str | None]:
    """``GET /v1/models`` → ``data[0].id``; ``GET /version`` → ``version``.

    Each lookup is independent; failures yield ``None`` for that slot.
    """
    base = base_url.rstrip("/")
    model: str | None = None
    version: str | None = None
    with httpx.Client(timeout=timeout) as client:
        try:
            resp = client.get(f"{base}/v1/models")
            if resp.status_code == 200:
                data = resp.json().get("data") or []
                if data and isinstance(data[0], dict):
                    model = data[0].get("id") or None
        except Exception as exc:
            log.debug("probe %s/v1/models failed: %s", base, exc)
        try:
            resp = client.get(f"{base}/version")
            if resp.status_code == 200:
                version = resp.json().get("version") or None
        except Exception as exc:
            log.debug("probe %s/version failed: %s", base, exc)
    return model, version
