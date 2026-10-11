"""Token usage reports (docs/token-governance.md, phase 1). Who sees what is decided in services/usage_reports.py."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from ..domain.models import UserPublic
from .deps import Container, current_user, get_container

router = APIRouter()


def _svc(c: Container) -> Any:
    return c.usage_reports


@router.get("/api/projects/{project_id}/usage")
async def project_usage(project_id: str, frm: str | None = Query(None, alias="from"), to: str | None = None, billable: str = "billable", person: str | None = None,
                        user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await _svc(c).project(user, project_id, frm, to, billable, person)


@router.get("/api/projects/{project_id}/usage/events")
async def project_usage_events(project_id: str, frm: str | None = Query(None, alias="from"), to: str | None = None, billable: str = "billable", person: str | None = None,
                               activity: str | None = None, status: str | None = None, limit: int = 100, offset: int = 0,
                               user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await _svc(c).events(user, project_id, frm, to, billable, person, activity, status, limit, offset)


@router.get("/api/projects/{project_id}/usage/export.csv")
async def project_usage_csv(project_id: str, frm: str | None = Query(None, alias="from"), to: str | None = None, billable: str = "billable", person: str | None = None,
                            user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> Response:
    body = await _svc(c).project_csv(user, project_id, frm, to, billable, person)
    return Response(body, media_type="text/csv", headers={"content-disposition": f'attachment; filename="token-usage-{project_id[:8]}.csv"'})


@router.get("/api/usage")
async def organisation_usage(frm: str | None = Query(None, alias="from"), to: str | None = None, billable: str = "billable",
                             user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await _svc(c).organisation(user, frm, to, billable)


@router.get("/api/usage/export.csv")
async def organisation_usage_csv(frm: str | None = Query(None, alias="from"), to: str | None = None, billable: str = "billable",
                                 user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> Response:
    return Response(await _svc(c).organisation_csv(user, frm, to, billable), media_type="text/csv",
                    headers={"content-disposition": 'attachment; filename="token-usage-organisation.csv"'})
