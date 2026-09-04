"""Persist tail positions so restarts resume without re-pushing."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from typing import Any

log = logging.getLogger(__name__)


class CursorStore:
    def __init__(self, path: str) -> None:
        self.path = path
        self._data: dict[str, dict[str, Any]] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                self._data = {k: v for k, v in data.items() if isinstance(v, dict)}
        except FileNotFoundError:
            pass
        except Exception as exc:
            log.warning("ignoring unreadable cursor file %s: %s", self.path, exc)

    def get(self, key: str) -> dict[str, Any] | None:
        value = self._data.get(key)
        return dict(value) if value is not None else None

    def set(self, key: str, value: dict[str, Any]) -> None:
        self._data[key] = dict(value)
        self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        directory = os.path.dirname(self.path) or "."
        try:
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".cursors-", dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh)
            os.replace(tmp, self.path)
            self._dirty = False
        except Exception as exc:
            log.warning("cannot save cursors to %s: %s", self.path, exc)

    async def run(self, stop: asyncio.Event, interval: float = 5.0) -> None:
        try:
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    pass
                self.save()
        finally:
            self.save()
