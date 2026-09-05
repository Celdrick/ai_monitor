"""Forward vLLM /start_profile and /stop_profile, then collect traces."""

from __future__ import annotations

import logging
import os

import httpx

from ..discovery.registry import ServiceRegistry
from ..services import ServiceInfo
from .artifacts import new_or_grown, register_file, snapshot_dir
from .tasks import Task

log = logging.getLogger(__name__)

PROFILE_HINT = (
    'vLLM /start_profile returned 404. Launch the server with '
    '--profiler-config \'{"profiler":"torch","torch_profiler_dir":"<dir>"}\''
)


def profiler_dir_for(svc: ServiceInfo, artifacts_dir: str) -> str:
    if svc.profiler_dir:
        return svc.profiler_dir
    return os.path.join(artifacts_dir, "profile", svc.name)


async def start_profile(task: Task, svc: ServiceInfo, artifacts_dir: str, snapshots: dict[str, dict]) -> None:
    url = svc.metrics_url.rstrip("/") + "/start_profile"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(url, timeout=15.0)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"start_profile request failed: {exc}") from exc
    if resp.status_code == 404:
        raise RuntimeError(PROFILE_HINT)
    if resp.status_code >= 400:
        raise RuntimeError(f"start_profile HTTP {resp.status_code}: {resp.text[:300]}")
    directory = profiler_dir_for(svc, artifacts_dir)
    os.makedirs(directory, exist_ok=True)
    snapshots[svc.name] = snapshot_dir(directory)


async def stop_profile(task: Task, svc: ServiceInfo, artifacts_dir: str, snapshots: dict[str, dict]) -> None:
    url = svc.metrics_url.rstrip("/") + "/stop_profile"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(url, timeout=600.0)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"stop_profile request failed: {exc}") from exc
    if resp.status_code == 404:
        raise RuntimeError(PROFILE_HINT)
    if resp.status_code >= 400:
        raise RuntimeError(f"stop_profile HTTP {resp.status_code}: {resp.text[:300]}")
    directory = profiler_dir_for(svc, artifacts_dir)
    before = snapshots.pop(svc.name, {})
    for path in new_or_grown(directory, before):
        register_file(task, path, "torch_trace")
    if not task.artifacts:
        raise RuntimeError(f"stop_profile succeeded but no new files in {directory}")
