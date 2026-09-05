"""In-memory debug task table with single-flight occupancy."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

log = logging.getLogger(__name__)

BUSY_TYPES = frozenset({"profile_start", "pyspy", "nsys", "msprof"})
# profile_start occupies until profile_stop finishes or start fails.


@dataclass
class Artifact:
    id: str
    type: str
    filename: str
    path: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
        }


@dataclass
class Task:
    id: str
    type: str
    service: str
    status: str = "pending"
    error: str | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    proc: asyncio.subprocess.Process | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "service": self.service,
            "status": self.status,
            "error": self.error,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


class BusyError(Exception):
    pass


class TaskManager:
    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}
        self.busy = False
        self.profiling: set[str] = set()
        self._lock = asyncio.Lock()

    def get(self, task_id: str) -> Task | None:
        return self.tasks.get(task_id)

    async def submit(
        self,
        typ: str,
        service: str,
        runner: Callable[[Task], Coroutine[Any, Any, None]],
    ) -> Task:
        async with self._lock:
            if typ == "profile_stop":
                pass
            elif typ == "profile_start":
                if self.busy or service in self.profiling:
                    raise BusyError("another debug task is running")
                self.busy = True
            else:
                if self.busy:
                    raise BusyError("another debug task is running")
                self.busy = True
            task = Task(id=uuid.uuid4().hex, type=typ, service=service)
            self.tasks[task.id] = task

        async def _wrap() -> None:
            task.status = "running"
            try:
                await runner(task)
                if task.status == "running":
                    task.status = "succeeded"
            except asyncio.CancelledError:
                task.status = "cancelled"
                task.error = task.error or "cancelled"
                raise
            except Exception as exc:
                log.warning("debug task %s failed: %s", task.id, exc)
                task.status = "failed"
                task.error = str(exc)
            finally:
                task.finished_at = time.time()
                async with self._lock:
                    if typ == "profile_start" and task.status == "succeeded":
                        self.profiling.add(service)
                        # stay busy until stop
                    else:
                        if typ == "profile_stop":
                            self.profiling.discard(service)
                        self.busy = bool(self.profiling)

        asyncio.create_task(_wrap(), name=f"debug-{task.id}")
        return task

    async def cancel(self, task: Task) -> None:
        task.cancel.set()
        if task.proc is not None and task.proc.returncode is None:
            try:
                task.proc.kill()
            except ProcessLookupError:
                pass
        task.error = "cancelled"
        if task.status in ("pending", "running"):
            task.status = "cancelled"
            task.finished_at = time.time()
            async with self._lock:
                if task.type == "profile_stop":
                    self.profiling.discard(task.service)
                if task.type == "profile_start":
                    self.profiling.discard(task.service)
                self.busy = bool(self.profiling)
