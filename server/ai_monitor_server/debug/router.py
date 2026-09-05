from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..agents.router import agent_status
from ..agents.tokens import decrypt_agent_token
from ..auth.deps import AdminUser, CurrentUser, SessionDep, SettingsDep
from ..models import Agent, Artifact, AuditLog, DebugTask, Service
from ..services.status import compute_status
from .agent_client import AgentControlError, control_request

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/debug", tags=["debug"])

TaskType = Literal[
    "profile_start", "profile_stop", "pyspy_dump", "pyspy_record", "nsys", "msprof"
]


class TaskCreate(BaseModel):
    service_id: int
    type: TaskType
    duration: int | None = Field(default=None, ge=5, le=60)


class ArtifactOut(BaseModel):
    id: int
    type: str
    filename: str
    size_bytes: int
    created_at: datetime


class TaskOut(BaseModel):
    id: int
    service_id: int
    type: str
    status: str
    error: str | None
    created_at: datetime
    finished_at: datetime | None
    artifacts: list[ArtifactOut] = []


AGENT_PATH = {
    "profile_start": ("POST", "/control/profile/start"),
    "profile_stop": ("POST", "/control/profile/stop"),
    "pyspy_dump": ("POST", "/control/pyspy"),
    "pyspy_record": ("POST", "/control/pyspy"),
    "nsys": ("POST", "/control/nsys"),
    "msprof": ("POST", "/control/msprof"),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _body(typ: TaskType, service_name: str, duration: int | None) -> dict[str, Any]:
    if typ == "pyspy_dump":
        return {"service": service_name, "mode": "dump"}
    if typ == "pyspy_record":
        return {"service": service_name, "mode": "record", "duration": duration or 15}
    if typ in ("nsys", "msprof"):
        return {"service": service_name, "duration": duration or 15}
    return {"service": service_name}


def _to_out(task: DebugTask, artifacts: list[Artifact]) -> TaskOut:
    return TaskOut(
        id=task.id,
        service_id=task.service_id,
        type=task.type,
        status=task.status,
        error=task.error,
        created_at=task.created_at,
        finished_at=task.finished_at,
        artifacts=[
            ArtifactOut(
                id=a.id,
                type=a.type,
                filename=a.filename,
                size_bytes=a.size_bytes,
                created_at=a.created_at,
            )
            for a in artifacts
        ],
    )


async def _service_bundle(session, service_id: int, settings) -> tuple[Service, Agent]:
    service = await session.get(Service, service_id)
    if service is None:
        raise HTTPException(status_code=404, detail="Service not found")
    agent = await session.get(Agent, service.agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent_status(agent, settings) != "online":
        raise HTTPException(status_code=409, detail="Agent is not online")
    if not agent.advertise_address:
        raise HTTPException(status_code=409, detail="Agent has no advertise_address")
    svc_status = compute_status(service, agent_status(agent, settings))
    if svc_status not in ("running", "degraded"):
        raise HTTPException(status_code=409, detail=f"Service is {svc_status}")
    return service, agent


def _plain_token(agent: Agent, secret: str) -> str:
    if not agent.token_enc:
        raise HTTPException(
            status_code=409,
            detail="Agent token is not stored; rotate the agent token once",
        )
    try:
        return decrypt_agent_token(agent.token_enc, secret)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/capabilities")
async def capabilities(
    service_id: int, session: SessionDep, settings: SettingsDep, _: CurrentUser
):
    service, agent = await _service_bundle(session, service_id, settings)
    token = _plain_token(agent, settings.jwt_secret)
    try:
        resp = await control_request(agent.advertise_address or "", token, "GET", "/control/capabilities")
    except AgentControlError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=resp.text[:300])
    caps = resp.json()
    vendor = (agent.hardware_vendor or "").lower()
    if vendor == "nvidia":
        caps["msprof"] = False
    if vendor == "ascend":
        caps["nsys"] = False
    return caps


@router.post("/tasks", response_model=TaskOut, status_code=status.HTTP_202_ACCEPTED)
async def create_task(
    body: TaskCreate,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    user: AdminUser,
):
    service, agent = await _service_bundle(session, body.service_id, settings)
    token = _plain_token(agent, settings.jwt_secret)
    method, path = AGENT_PATH[body.type]
    try:
        resp = await control_request(
            agent.advertise_address or "",
            token,
            method,
            path,
            json=_body(body.type, service.name, body.duration),
        )
    except AgentControlError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    if resp.status_code == 409:
        raise HTTPException(status_code=409, detail=resp.json().get("detail", resp.text))
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=resp.text[:400])
    payload = resp.json()
    task = DebugTask(
        service_id=service.id,
        type=body.type,
        status="pending",
        requested_by=user.id,
        agent_task_id=payload.get("id"),
    )
    session.add(task)
    session.add(
        AuditLog(
            user_id=user.id,
            action=f"debug.{body.type}",
            target=f"service:{service.id}",
            payload_json={"duration": body.duration, "agent_task_id": payload.get("id")},
        )
    )
    await session.commit()
    await session.refresh(task)
    asyncio.create_task(
        _poll_and_fetch(request.app, task.id, agent.id, settings.jwt_secret),
        name=f"debug-poll-{task.id}",
    )
    return _to_out(task, [])


@router.get("/tasks", response_model=list[TaskOut])
async def list_tasks(session: SessionDep, _: CurrentUser, service_id: int | None = None):
    q = select(DebugTask).order_by(DebugTask.id.desc())
    if service_id is not None:
        q = q.where(DebugTask.service_id == service_id)
    tasks = (await session.execute(q.limit(100))).scalars().all()
    return [await _task_out(session, t) for t in tasks]


@router.get("/tasks/{task_id}", response_model=TaskOut)
async def get_task(task_id: int, session: SessionDep, _: CurrentUser):
    task = await session.get(DebugTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return await _task_out(session, task)


@router.get("/artifacts/{artifact_id}")
async def download_artifact(artifact_id: int, session: SessionDep, _: AdminUser):
    art = await session.get(Artifact, artifact_id)
    if art is None or not os.path.isfile(art.path):
        raise HTTPException(status_code=404, detail="Artifact not found")
    return FileResponse(art.path, filename=art.filename)


async def _task_out(session, task: DebugTask) -> TaskOut:
    arts = (
        await session.execute(select(Artifact).where(Artifact.task_id == task.id))
    ).scalars().all()
    return _to_out(task, list(arts))


async def _poll_and_fetch(app, task_id: int, agent_id: int, secret: str) -> None:
    settings = app.state.settings
    interval = settings.debug_poll_interval_seconds
    try:
        for _ in range(600):
            await asyncio.sleep(interval)
            async with app.state.sessionmaker() as session:
                task = await session.get(DebugTask, task_id)
                agent = await session.get(Agent, agent_id)
                if task is None or agent is None or not task.agent_task_id:
                    return
                token = decrypt_agent_token(agent.token_enc or "", secret)
                try:
                    resp = await control_request(
                        agent.advertise_address or "",
                        token,
                        "GET",
                        f"/control/tasks/{task.agent_task_id}",
                    )
                except AgentControlError as exc:
                    task.status = "failed"
                    task.error = exc.detail
                    task.finished_at = _utcnow()
                    await session.commit()
                    return
                if resp.status_code >= 400:
                    task.status = "failed"
                    task.error = resp.text[:400]
                    task.finished_at = _utcnow()
                    await session.commit()
                    return
                body = resp.json()
                st = body.get("status")
                if st in ("pending", "running"):
                    task.status = st
                    await session.commit()
                    continue
                task.status = st
                task.error = body.get("error")
                task.finished_at = _utcnow()
                if st == "succeeded":
                    try:
                        await _pull_artifacts(session, task, agent, token, body.get("artifacts") or [], settings)
                    except Exception as exc:
                        task.status = "failed"
                        task.error = str(exc)
                await session.commit()
                return
        async with app.state.sessionmaker() as session:
            task = await session.get(DebugTask, task_id)
            if task and task.status in ("pending", "running"):
                task.status = "failed"
                task.error = "timed out waiting for agent task"
                task.finished_at = _utcnow()
                await session.commit()
    except Exception:
        log.exception("debug poll failed for task %s", task_id)


async def _pull_artifacts(session, task, agent, token, items: list[dict], settings) -> None:
    service = await session.get(Service, task.service_id)
    host = agent.host if agent else "unknown"
    name = service.name if service else "unknown"
    dest_dir = os.path.join(settings.artifacts_dir, host, name, str(task.id))
    os.makedirs(dest_dir, exist_ok=True)
    for item in items:
        aid = item.get("id")
        resp = await control_request(
            agent.advertise_address or "",
            token,
            "GET",
            f"/control/artifacts/{aid}",
            timeout=120.0,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"download artifact {aid} failed: HTTP {resp.status_code}")
        if len(resp.content) > settings.artifact_max_bytes:
            raise RuntimeError(f"artifact {aid} exceeds {settings.artifact_max_bytes} bytes")
        filename = item.get("filename") or f"{aid}.bin"
        path = os.path.join(dest_dir, filename)
        with open(path, "wb") as fh:
            fh.write(resp.content)
        session.add(
            Artifact(
                task_id=task.id,
                type=item.get("type") or "file",
                filename=filename,
                path=path,
                size_bytes=len(resp.content),
            )
        )
