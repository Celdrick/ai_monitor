"""FastAPI router mounted on the agent app."""

from __future__ import annotations

import os
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..discovery.registry import ServiceRegistry
from ..services import ServiceInfo
from . import profile as profile_mod
from .runners import clamp_duration, run_msprof, run_nsys, run_pyspy, which
from .tasks import BusyError, TaskManager

router = APIRouter(prefix="/control", tags=["control"])


class ServiceBody(BaseModel):
    service: str


class PyspyBody(BaseModel):
    service: str
    mode: Literal["dump", "record"] = "dump"
    duration: int | None = Field(default=None, ge=5, le=60)


class DurationBody(BaseModel):
    service: str
    duration: int | None = Field(default=None, ge=5, le=60)


def require_agent_token(request: Request) -> None:
    expected = request.app.state.config.agent_token
    header = request.headers.get("Authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or token.strip() != expected:
        raise HTTPException(status_code=401, detail="Invalid agent token")


def _registry(request: Request) -> ServiceRegistry:
    return request.app.state.services


def _manager(request: Request) -> TaskManager:
    return request.app.state.tasks


def _find(registry: ServiceRegistry, name: str) -> ServiceInfo:
    for svc in registry.snapshot():
        if svc.name == name:
            return svc
    raise HTTPException(status_code=404, detail=f"unknown service {name}")


@router.get("/capabilities")
def capabilities(request: Request, _: None = Depends(require_agent_token)) -> dict[str, bool]:
    fake = bool(getattr(request.app.state, "fake_debug", False))
    return {
        "profile": True,
        "pyspy": fake or which("py-spy") is not None,
        "nsys": fake or which("nsys") is not None,
        "msprof": fake or which("msprof") is not None,
    }


@router.post("/profile/start", status_code=202)
async def profile_start(body: ServiceBody, request: Request, _: None = Depends(require_agent_token)):
    registry, manager = _registry(request), _manager(request)
    svc = _find(registry, body.service)
    artifacts_dir = request.app.state.config.artifacts_dir
    snapshots = request.app.state.profile_snapshots

    async def run(task):
        await profile_mod.start_profile(task, svc, artifacts_dir, snapshots)

    return await _submit(manager, "profile_start", body.service, run)


@router.post("/profile/stop", status_code=202)
async def profile_stop(body: ServiceBody, request: Request, _: None = Depends(require_agent_token)):
    registry, manager = _registry(request), _manager(request)
    svc = _find(registry, body.service)
    artifacts_dir = request.app.state.config.artifacts_dir
    snapshots = request.app.state.profile_snapshots

    async def run(task):
        await profile_mod.stop_profile(task, svc, artifacts_dir, snapshots)

    return await _submit(manager, "profile_stop", body.service, run)


@router.post("/pyspy", status_code=202)
async def pyspy(body: PyspyBody, request: Request, _: None = Depends(require_agent_token)):
    svc = _find(_registry(request), body.service)
    dest = os.path.join(request.app.state.config.artifacts_dir, "pyspy", body.service)
    duration = clamp_duration(body.duration)
    fake = bool(request.app.state.fake_debug)

    async def run(task):
        await run_pyspy(task, svc, body.mode, duration, dest, fake)

    return await _submit(_manager(request), "pyspy", body.service, run)


@router.post("/nsys", status_code=202)
async def nsys(body: DurationBody, request: Request, _: None = Depends(require_agent_token)):
    svc = _find(_registry(request), body.service)
    dest = os.path.join(request.app.state.config.artifacts_dir, "nsys", body.service)
    duration = clamp_duration(body.duration)
    fake = bool(request.app.state.fake_debug)

    async def run(task):
        await run_nsys(task, svc, duration, dest, fake)

    return await _submit(_manager(request), "nsys", body.service, run)


@router.post("/msprof", status_code=202)
async def msprof(body: DurationBody, request: Request, _: None = Depends(require_agent_token)):
    svc = _find(_registry(request), body.service)
    dest = os.path.join(request.app.state.config.artifacts_dir, "msprof", body.service)
    duration = clamp_duration(body.duration)
    fake = bool(request.app.state.fake_debug)

    async def run(task):
        await run_msprof(task, svc, duration, dest, fake)

    return await _submit(_manager(request), "msprof", body.service, run)


@router.get("/tasks/{task_id}")
def get_task(task_id: str, request: Request, _: None = Depends(require_agent_token)):
    task = _manager(request).get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task.to_dict()


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request, _: None = Depends(require_agent_token)):
    manager = _manager(request)
    task = manager.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    await manager.cancel(task)
    return task.to_dict()


@router.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: str, request: Request, _: None = Depends(require_agent_token)):
    for task in request.app.state.tasks.tasks.values():
        for art in task.artifacts:
            if art.id == artifact_id:
                if not os.path.isfile(art.path):
                    raise HTTPException(status_code=404, detail="artifact file missing")
                return FileResponse(art.path, filename=art.filename)
    raise HTTPException(status_code=404, detail="artifact not found")


async def _submit(manager: TaskManager, typ: str, service: str, runner):
    try:
        task = await manager.submit(typ, service, runner)
    except BusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": task.id, "status": task.status}
