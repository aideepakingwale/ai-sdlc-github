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
