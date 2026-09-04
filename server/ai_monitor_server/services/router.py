from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from ..agents.router import _as_utc, _get_or_404, _utcnow, agent_status
from ..auth.deps import CurrentUser, SessionDep, SettingsDep
from ..config import Settings
from ..models import Agent, Service
from .schemas import ServiceDetail, ServiceOut
from .status import compute_status

router = APIRouter(tags=["services"])

ActiveFilter = Literal["all", "true", "false"]


def _out_fields(service: Service, agent: Agent, settings: Settings, now: datetime) -> dict:
    return {
        "id": service.id,
        "agent_id": service.agent_id,
        "host": agent.host,
        "name": service.name,
        "source": service.source,
        "port": service.port,
        "metrics_url": service.metrics_url,
        "pid": service.pid,
        "container_name": service.container_name,
        "model": service.model,
        "vllm_version": service.vllm_version,
        "started_at": _as_utc(service.started_at),
        "log_source": service.log_source,
        "scrape_ok": service.scrape_ok,
        "active": service.active,
        "last_seen_at": _as_utc(service.last_seen_at),
        "status": compute_status(service, agent_status(agent, settings, now)),
    }


def to_out(service: Service, agent: Agent, settings: Settings, now: datetime) -> ServiceOut:
    return ServiceOut(**_out_fields(service, agent, settings, now))


def to_detail(service: Service, agent: Agent, settings: Settings, now: datetime) -> ServiceDetail:
    return ServiceDetail(
        **_out_fields(service, agent, settings, now),
        cmdline=service.cmdline,
        cwd=service.cwd,
        env=dict(service.env_json or {}),
        container_id=service.container_id,
        log_path=service.log_path,
        profiler_dir=service.profiler_dir,
        first_seen_at=_as_utc(service.first_seen_at),
    )


def _apply_active(stmt, active: ActiveFilter):
    if active == "true":
        return stmt.where(Service.active.is_(True))
    if active == "false":
        return stmt.where(Service.active.is_(False))
    return stmt


async def _list(session, settings: Settings, stmt) -> list[ServiceOut]:
    rows = (await session.execute(stmt.order_by(Agent.host, Service.name))).all()
    now = _utcnow()
    return [to_out(service, agent, settings, now) for service, agent in rows]


@router.get("/api/services", response_model=list[ServiceOut])
async def list_services(
    session: SessionDep,
    settings: SettingsDep,
    _: CurrentUser,
    active: ActiveFilter = Query("all"),
):
    stmt = _apply_active(select(Service, Agent).join(Agent, Service.agent_id == Agent.id), active)
    return await _list(session, settings, stmt)


@router.get("/api/services/{service_id}", response_model=ServiceDetail)
async def get_service(
    service_id: int, session: SessionDep, settings: SettingsDep, _: CurrentUser
):
    row = (
        await session.execute(
            select(Service, Agent)
            .join(Agent, Service.agent_id == Agent.id)
            .where(Service.id == service_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    service, agent = row
    return to_detail(service, agent, settings, _utcnow())


@router.get("/api/agents/{agent_id}/services", response_model=list[ServiceOut])
async def list_agent_services(
    agent_id: int,
    session: SessionDep,
    settings: SettingsDep,
    _: CurrentUser,
    active: ActiveFilter = Query("all"),
):
    await _get_or_404(session, agent_id)
    stmt = _apply_active(
        select(Service, Agent)
        .join(Agent, Service.agent_id == Agent.id)
        .where(Service.agent_id == agent_id),
        active,
    )
    return await _list(session, settings, stmt)
