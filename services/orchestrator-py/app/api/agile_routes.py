"""Agile delivery REST API: methodology, sprints, releases, backlog, proposals, Jira sync."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..domain.errors import SdlcError
from ..domain.models import UserPublic
from .deps import Container, current_user, get_container

router = APIRouter()


class EnableAgileRequest(BaseModel):
    methodology: Literal["scrum", "kanban"]
    sprintDays: int = Field(default=14, ge=1, le=42)
    defaultCapacity: float = Field(default=30, ge=0, le=10_000)
    wipLimit: int | None = Field(default=None, ge=1, le=100)
    indexStrategy: Literal["index-branch", "default-branch"] = "index-branch"
    autoMinScore: int = Field(default=80, ge=0, le=100)


class AgileSettingsRequest(BaseModel):
    sprintDays: int | None = Field(default=None, ge=1, le=42)
    defaultCapacity: float | None = Field(default=None, ge=0, le=10_000)
    wipLimit: int | None = Field(default=None, ge=1, le=100)
    clearWipLimit: bool = False
    indexStrategy: Literal["index-branch", "default-branch"] | None = None
    autoMinScore: int | None = Field(default=None, ge=0, le=100)


class StartSprintRequest(BaseModel):
    goal: str = Field(default="", max_length=240)
    capacity: float | None = Field(default=None, ge=0, le=10_000)
    startsOn: dt.date | None = None


@router.get("/api/projects/{project_id}/agile")
async def agile_overview(
    project_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container),
) -> dict[str, Any]:
    return await c.agile.overview(project_id, user)


@router.post("/api/projects/{project_id}/agile/enable")
async def enable_agile(
    project_id: str, body: EnableAgileRequest,
    user: UserPublic = Depends(current_user), c: Container = Depends(get_container),
) -> dict[str, Any]:
    return await c.agile.enable(
        project_id, user, methodology=body.methodology, sprint_days=body.sprintDays,
        default_capacity=body.defaultCapacity, wip_limit=body.wipLimit, index_strategy=body.indexStrategy,
        auto_min_score=body.autoMinScore)


@router.patch("/api/projects/{project_id}/agile/settings")
async def agile_settings(
    project_id: str, body: AgileSettingsRequest,
    user: UserPublic = Depends(current_user), c: Container = Depends(get_container),
) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    if body.sprintDays is not None:
        changes["sprint_days"] = body.sprintDays
    if body.defaultCapacity is not None:
        changes["default_capacity"] = body.defaultCapacity
    if body.clearWipLimit:
        changes["wip_limit"] = None
    elif body.wipLimit is not None:
        changes["wip_limit"] = body.wipLimit
    if body.indexStrategy is not None:
        changes["index_strategy"] = body.indexStrategy
    if body.autoMinScore is not None:
        changes["auto_min_score"] = body.autoMinScore
    if not changes:
        raise SdlcError("VALIDATION_FAILED", "Nothing to change")
    return await c.agile.update_settings(project_id, user, changes)


@router.post("/api/projects/{project_id}/agile/sprints")
async def start_sprint(
    project_id: str, body: StartSprintRequest,
    user: UserPublic = Depends(current_user), c: Container = Depends(get_container),
) -> dict[str, Any]:
    return await c.agile.start_sprint(
        project_id, user, goal=body.goal, capacity=body.capacity, starts_on=body.startsOn)


@router.post("/api/projects/{project_id}/agile/sprints/{iteration_id}/cancel")
async def cancel_sprint(
    project_id: str, iteration_id: str,
    user: UserPublic = Depends(current_user), c: Container = Depends(get_container),
) -> dict[str, Any]:
    return await c.agile.cancel_sprint(project_id, user, iteration_id)


@router.post("/api/projects/{project_id}/agile/releases/{release_id}/harden")
async def harden_release(
    project_id: str, release_id: str,
    user: UserPublic = Depends(current_user), c: Container = Depends(get_container),
) -> dict[str, Any]:
    return await c.agile.start_release_hardening(project_id, user, release_id)


# ------------------------------------------------------------------ backlog
class BacklogCreate(BaseModel):
    type: Literal["epic", "story", "bug", "task"] = "story"
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=8000)
    acceptanceCriteria: list[str] = Field(default_factory=list, max_length=20)
    estimate: float | None = None
    components: list[str] = Field(default_factory=list, max_length=10)
    labels: list[str] = Field(default_factory=list, max_length=20)
    epicKey: str | None = None


class BacklogPatch(BaseModel):
    expectedVersion: int | None = None
    type: Literal["epic", "story", "bug", "task"] | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=8000)
    acceptanceCriteria: list[str] | None = Field(default=None, max_length=20)
    estimate: float | None = None
    clearEstimate: bool = False
    components: list[str] | None = Field(default=None, max_length=10)
    labels: list[str] | None = Field(default=None, max_length=20)
    epicKey: str | None = None
    clearEpic: bool = False


class StatusBody(BaseModel):
    status: str
    expectedVersion: int | None = None


class MoveBody(BaseModel):
    before: str | None = None
    after: str | None = None


class SprintAddBody(BaseModel):
    force: bool = False


@router.get("/api/projects/{project_id}/agile/backlog")
async def list_backlog(
    project_id: str, status: str | None = None, iteration: str | None = None, q: str | None = None,
    user: UserPublic = Depends(current_user), c: Container = Depends(get_container),
) -> dict[str, Any]:
    statuses = [s for s in (status or "").split(",") if s] or None
    return await c.extras["backlog"].list(project_id, user, status=statuses, iteration_id=iteration, q=q)


@router.post("/api/projects/{project_id}/agile/backlog")
async def create_backlog_item(
    project_id: str, body: BacklogCreate,
    user: UserPublic = Depends(current_user), c: Container = Depends(get_container),
) -> dict[str, Any]:
    return await c.extras["backlog"].create(project_id, user, body.model_dump(exclude_none=True))


@router.get("/api/projects/{project_id}/agile/backlog/{ref}")
async def get_backlog_item(
    project_id: str, ref: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container),
) -> dict[str, Any]:
    return await c.extras["backlog"].get(project_id, user, ref)


@router.patch("/api/projects/{project_id}/agile/backlog/{ref}")
async def patch_backlog_item(
    project_id: str, ref: str, body: BacklogPatch,
    user: UserPublic = Depends(current_user), c: Container = Depends(get_container),
) -> dict[str, Any]:
    raw = body.model_dump(exclude={"expectedVersion", "clearEstimate", "clearEpic"}, exclude_none=True)
    if body.clearEstimate:
        raw["estimate"] = None
    if body.clearEpic:
        raw["epicKey"] = None
    if not raw:
        raise SdlcError("VALIDATION_FAILED", "Nothing to change")
    return await c.extras["backlog"].update(project_id, user, ref, raw, expected_version=body.expectedVersion)


@router.post("/api/projects/{project_id}/agile/backlog/{ref}/status")
async def backlog_status(
    project_id: str, ref: str, body: StatusBody,
    user: UserPublic = Depends(current_user), c: Container = Depends(get_container),
) -> dict[str, Any]:
    return await c.extras["backlog"].set_status(project_id, user, ref, body.status, expected_version=body.expectedVersion)


@router.post("/api/projects/{project_id}/agile/backlog/{ref}/move")
async def backlog_move(
    project_id: str, ref: str, body: MoveBody,
    user: UserPublic = Depends(current_user), c: Container = Depends(get_container),
) -> dict[str, Any]:
    return await c.extras["backlog"].move(project_id, user, ref, before=body.before, after=body.after)


@router.post("/api/projects/{project_id}/agile/backlog/{ref}/sprint")
async def backlog_add_to_sprint(
    project_id: str, ref: str, body: SprintAddBody,
    user: UserPublic = Depends(current_user), c: Container = Depends(get_container),
) -> dict[str, Any]:
    return await c.extras["backlog"].add_to_sprint(project_id, user, ref, force=body.force)


@router.delete("/api/projects/{project_id}/agile/backlog/{ref}/sprint")
async def backlog_remove_from_sprint(
    project_id: str, ref: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container),
) -> dict[str, Any]:
    return await c.extras["backlog"].remove_from_sprint(project_id, user, ref)


# ------------------------------------------------------------------ proposals
class ProposalPatch(BaseModel):
    payload: dict[str, Any]
    version: int


@router.get("/api/projects/{project_id}/agile/proposals")
async def latest_proposal(
    project_id: str, phase: int, kind: Literal["refine", "plan", "delta"],
    user: UserPublic = Depends(current_user), c: Container = Depends(get_container),
) -> dict[str, Any]:
    return {"proposal": await c.extras["proposals"].latest(project_id, user, phase, kind)}


@router.patch("/api/projects/{project_id}/agile/proposals/{proposal_id}")
async def edit_proposal(
    project_id: str, proposal_id: str, body: ProposalPatch,
    user: UserPublic = Depends(current_user), c: Container = Depends(get_container),
) -> dict[str, Any]:
    return await c.extras["proposals"].edit(project_id, user, proposal_id, body.payload, body.version)


# ------------------------------------------------------------------ Jira sync + project memory
class JiraSyncBody(BaseModel):
    full: bool = False


@router.get("/api/projects/{project_id}/agile/jira")
async def jira_status(
    project_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container),
) -> dict[str, Any]:
    return await c.extras["jira"].status(project_id, user)


@router.post("/api/projects/{project_id}/agile/reconcile")
async def reconcile(
    project_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container),
) -> dict[str, Any]:
    """Repair any half-finished approval bookkeeping (idempotent). Safe to call at any time."""
    await c.agile.assert_can_run(project_id, user)
    return {"revisited": await c.agile.reconcile(project_id)}


@router.post("/api/projects/{project_id}/agile/jira/sync")
async def jira_sync(
    project_id: str, body: JiraSyncBody,
    user: UserPublic = Depends(current_user), c: Container = Depends(get_container),
) -> dict[str, Any]:
    return await c.extras["jira"].sync(project_id, user, full=body.full)


@router.get("/api/projects/{project_id}/agile/index")
async def index_status(
    project_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container),
) -> dict[str, Any]:
    """State of the `.devmind/` project memory: tiers, unpublished changes, drift, specs."""
    await c.authz.assert_project_access(project_id, user)
    return await c.extras["index"].status(project_id)
