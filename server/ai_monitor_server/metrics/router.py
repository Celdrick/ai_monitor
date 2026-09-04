from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status

from ..auth.deps import CurrentUser
from .templates import render
from .vm_client import VMClient, VMError

router = APIRouter(prefix="/api/metrics", tags=["metrics"])

ALLOWED_PARAMS = ("host", "index")


def get_vm_client(request: Request) -> VMClient:
    return request.app.state.vm_client


def _render_or_400(template: str, params: dict[str, str]) -> str:
    try:
        return render(template, params)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"unknown template {template!r}"
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _params(host: str | None, index: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if host is not None:
        out["host"] = host
    if index is not None:
        out["index"] = index
    return out


@router.get("/query")
async def query(
    request: Request,
    _: CurrentUser,
    template: str = Query(...),
    host: str | None = Query(None),
    index: str | None = Query(None),
    time: float | None = Query(None),
) -> Any:
    expr = _render_or_400(template, _params(host, index))
    try:
        return await get_vm_client(request).query(expr, time)
    except VMError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.get("/query_range")
async def query_range(
    request: Request,
    _: CurrentUser,
    template: str = Query(...),
    start: float = Query(...),
    end: float = Query(...),
    step: str = Query(...),
    host: str | None = Query(None),
    index: str | None = Query(None),
) -> Any:
    expr = _render_or_400(template, _params(host, index))
    try:
        return await get_vm_client(request).query_range(expr, start, end, step)
    except VMError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
