import asyncio
import json
import logging
import time
from typing import Literal

import jwt
import websockets
from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, status
from starlette.websockets import WebSocketDisconnect, WebSocketState

from ..auth.deps import CurrentUser
from ..auth.security import decode_token
from ..models import User
from .logql import build_logql
from .loki_client import LokiClient, LokiError, parse_streams

log = logging.getLogger(__name__)

router = APIRouter(tags=["logs"])

MAX_LIMIT = 5000
DEFAULT_RANGE_SECONDS = 3600
TAIL_LOOKBACK_NS = 60 * 1_000_000_000
TAIL_LIMIT = 100

WS_UNAUTHORIZED = 4401
WS_BAD_REQUEST = 4400
WS_UPSTREAM_ERROR = 4502


def get_loki(request_or_ws) -> LokiClient:
    return request_or_ws.app.state.loki


def _logql_or_400(host: str, service: str, levels: list[str], q: str | None) -> str:
    try:
        return build_logql(host, service, levels, q)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/api/logs/query")
async def query_logs(
    request: Request,
    _: CurrentUser,
    host: str = Query(...),
    service: str = Query(...),
    level: list[str] = Query([]),
    q: str | None = Query(None),
    start: float | None = Query(None),
    end: float | None = Query(None),
    limit: int = Query(500, ge=1),
    direction: Literal["backward", "forward"] = Query("backward"),
) -> dict:
    query = _logql_or_400(host, service, level, q)
    limit = min(limit, MAX_LIMIT)
    if end is None:
        end = time.time()
    if start is None:
        start = end - DEFAULT_RANGE_SECONDS
    if start > end:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="start must be <= end")
    try:
        lines = await get_loki(request).query_range(
            query, int(start * 1e9), int(end * 1e9), limit, direction
        )
    except LokiError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    return {"lines": lines, "has_more": len(lines) >= limit}


async def _authenticate_ws(ws: WebSocket, token: str | None) -> User | None:
    if not token:
        return None
    try:
        payload = decode_token(token, ws.app.state.settings.jwt_secret)
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != "access":
        return None
    try:
        user_id = int(payload.get("sub", ""))
    except ValueError:
        return None
    async with ws.app.state.sessionmaker() as session:
        return await session.get(User, user_id)


async def _close_quietly(ws: WebSocket, code: int) -> None:
    if ws.client_state == WebSocketState.CONNECTED:
        try:
            await ws.close(code=code)
        except Exception:  # pragma: no cover - client already gone
            pass


async def _wait_client_close(ws: WebSocket) -> None:
    """Resolve when the browser closes its side (we never expect inbound data)."""
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                return
    except (WebSocketDisconnect, RuntimeError):
        return


async def _pump_loki(upstream, ws: WebSocket) -> None:
    async for raw in upstream:
        try:
            frame = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(frame, dict):
            continue
        for row in parse_streams(frame.get("streams")):
            await ws.send_json(row)


@router.websocket("/api/logs/tail")
async def tail_logs(
    ws: WebSocket,
    host: str = Query(...),
    service: str = Query(...),
    level: list[str] = Query([]),
    q: str | None = Query(None),
    token: str | None = Query(None),
):
    await ws.accept()
    user = await _authenticate_ws(ws, token)
    if user is None:
        await ws.close(code=WS_UNAUTHORIZED)
        return
    try:
        query = build_logql(host, service, level, q)
    except ValueError:
        await ws.close(code=WS_BAD_REQUEST)
        return

    loki = get_loki(ws)
    url = loki.tail_url(query, start_ns=time.time_ns() - TAIL_LOOKBACK_NS, limit=TAIL_LIMIT)
    try:
        async with websockets.connect(url) as upstream:
            pump = asyncio.create_task(_pump_loki(upstream, ws))
            watch = asyncio.create_task(_wait_client_close(ws))
            done, pending = await asyncio.wait({pump, watch}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in pending:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            if pump in done:
                exc = pump.exception()
                if exc is not None and not isinstance(exc, websockets.ConnectionClosed):
                    log.warning("loki tail pump failed: %s", exc)
                    await _close_quietly(ws, WS_UPSTREAM_ERROR)
                    return
    except (OSError, websockets.WebSocketException, asyncio.TimeoutError) as exc:
        log.warning("cannot connect to loki tail %s: %s", url, exc)
        await _close_quietly(ws, WS_UPSTREAM_ERROR)
        return
    await _close_quietly(ws, status.WS_1000_NORMAL_CLOSURE)
