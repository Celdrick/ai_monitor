import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..auth.deps import AdminUser, CurrentUser, SessionDep, SettingsDep, bearer_token
from ..config import Settings
from ..models import Agent
from .file_sd import write_file_sd
from .schemas import AgentCreate, AgentOut, AgentStatus, AgentWithToken, HeartbeatRequest
from .tokens import generate_agent_token, hash_agent_token

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def agent_status(agent: Agent, settings: Settings, now: datetime | None = None) -> AgentStatus:
    last_seen = _as_utc(agent.last_seen_at)
    if last_seen is None:
        return "never"
    now = now or _utcnow()
    if now - last_seen <= timedelta(seconds=settings.agent_offline_seconds):
        return "online"
    return "offline"


def to_out(agent: Agent, settings: Settings, now: datetime | None = None) -> AgentOut:
    return AgentOut(
        id=agent.id,
        host=agent.host,
        ip=agent.ip,
        advertise_address=agent.advertise_address,
        version=agent.version,
        hardware_vendor=agent.hardware_vendor,
        device_count=agent.device_count,
        last_seen_at=_as_utc(agent.last_seen_at),
        status=agent_status(agent, settings, now),
    )


async def _get_or_404(session, agent_id: int) -> Agent:
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return agent


@router.post("", response_model=AgentWithToken, status_code=status.HTTP_201_CREATED)
async def create_agent(body: AgentCreate, session: SessionDep, _: AdminUser):
    token = generate_agent_token()
    agent = Agent(host=body.host, token_hash=hash_agent_token(token))
    session.add(agent)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Host already registered"
        )
    return AgentWithToken(id=agent.id, host=agent.host, token=token)


@router.post("/{agent_id}/rotate-token", response_model=AgentWithToken)
async def rotate_token(agent_id: int, session: SessionDep, _: AdminUser):
    agent = await _get_or_404(session, agent_id)
    token = generate_agent_token()
    agent.token_hash = hash_agent_token(token)
    await session.commit()
    return AgentWithToken(id=agent.id, host=agent.host, token=token)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(agent_id: int, session: SessionDep, _: AdminUser):
    agent = await _get_or_404(session, agent_id)
    await session.delete(agent)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
async def heartbeat(
    body: HeartbeatRequest, request: Request, session: SessionDep, settings: SettingsDep
):
    token = bearer_token(request)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Agent token required"
        )
    agent = (
        await session.execute(
            select(Agent).where(Agent.token_hash == hash_agent_token(token))
        )
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid agent token"
        )

    agent.ip = body.ip
    agent.listen_port = body.listen_port
    agent.advertise_address = body.advertise_address
    agent.version = body.agent_version
    agent.hardware_vendor = body.hardware_vendor
    agent.device_count = body.device_count
    agent.last_seen_at = _utcnow()
    await session.commit()

    seen = (
        await session.execute(select(Agent).where(Agent.last_seen_at.is_not(None)))
    ).scalars().all()
    try:
        write_file_sd(seen, settings.file_sd_path)
    except OSError:
        log.exception("failed to write file_sd to %s", settings.file_sd_path)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("", response_model=list[AgentOut])
async def list_agents(session: SessionDep, settings: SettingsDep, _: CurrentUser):
    agents = (await session.execute(select(Agent).order_by(Agent.id))).scalars().all()
    now = _utcnow()
    return [to_out(a, settings, now) for a in agents]


@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(agent_id: int, session: SessionDep, settings: SettingsDep, _: CurrentUser):
    agent = await _get_or_404(session, agent_id)
    return to_out(agent, settings)
