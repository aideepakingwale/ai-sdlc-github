"""Token pools (docs/token-governance.md, phase 3): a project's balance for its members, and the allocation console for administrators."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..domain.models import UserPublic
from .deps import Container, current_user, get_container

router = APIRouter()


class AllocateBody(BaseModel):
    tokens: int
    kind: str = "one_off"            # one_off | monthly
    note: str = ""
    countFrom: str = "now"           # now | month_start (only used the first time a pool is allocated to)


@router.get("/api/projects/{project_id}/token-pool")
async def project_pool(project_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.token_pools.project(user, project_id)


@router.get("/api/token-pools")
async def pools_overview(user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.token_pools.overview(user)


class GateModeBody(BaseModel):
    mode: str


@router.get("/api/token-gate")
async def gate_status(user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.token_pools.gate_status(user, c.token_gate)


@router.put("/api/token-gate")
async def set_gate_mode(body: GateModeBody, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    """observe (record what would be refused), enforce (refuse new work at zero or below) or off. Administrators."""
    return await c.token_pools.set_gate_mode(user, c.token_gate, body.mode)


@router.get("/api/token-pools/{scope}/{scope_id}")
async def pool_detail(scope: str, scope_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.token_pools.detail(user, scope, scope_id)


@router.post("/api/token-pools/{scope}/{scope_id}/allocations")
async def allocate(scope: str, scope_id: str, body: AllocateBody, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict[str, Any]:
    return await c.token_pools.allocate(user, scope, scope_id, body.tokens, body.kind, body.note, body.countFrom)


@router.post("/api/token-pools/{scope}/{scope_id}/recompute")
async def recompute(scope: str, scope_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.token_pools.recompute(user, scope, scope_id)


@router.delete("/api/token-schedules/{schedule_id}")
async def stop_schedule(schedule_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    await c.token_pools.stop_schedule(user, schedule_id)
    return {"stopped": True}


class RequestBody(BaseModel):
    note: str = ""


@router.post("/api/projects/{project_id}/token-requests")
async def request_tokens(project_id: str, body: RequestBody, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    """Ask the administrators for more tokens (stays available when the project's tokens are used up)."""
    return await c.token_pools.request_tokens(user, project_id, body.note)


@router.get("/api/token-requests")
async def token_requests(user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return {"requests": await c.token_pools.requests(user)}


@router.post("/api/token-requests/{request_id}/dismiss")
async def dismiss_token_request(request_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    await c.token_pools.dismiss_request(user, request_id)
    return {"dismissed": True}
