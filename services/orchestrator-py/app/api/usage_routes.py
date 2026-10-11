"""Token usage reports (docs/token-governance.md, phase 1). Who sees what is decided in services/usage_reports.py."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from datetime import date

from pydantic import BaseModel

from ..domain.errors import SdlcError
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


# ------------------------------------------------------------------------------ reconciliation with AWS (administrators)
def _admin(user: UserPublic) -> None:
    if user.role != "SUPER_ADMIN":
        raise SdlcError("FORBIDDEN", "Reconciliation with AWS is for administrators")


def _run_json(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "day": r["day"].isoformat(), "status": r["status"], "source": r["source"], "triggeredBy": r["triggered_by"], "finishedAt": r["finished_at"].isoformat() if r["finished_at"] else None,
        "threshold": float(r["threshold"]), "convention": r["convention"], "ledgerTokens": r["ledger_tokens"], "awsTokens": r["aws_tokens"],
        "drift": float(r["drift"]) if r["drift"] is not None else None, "matched": r["matched"], "mismatched": r["mismatched"], "missingInLedger": r["missing_in_ledger"],
        "missingInAws": r["missing_in_aws"], "unmatchableLedger": r["unmatchable_ledger"], "estimatedPairs": r["estimated_pairs"], "byModel": r["by_model"],
        "note": r["note"], "error": r["error"],
    }


@router.get("/api/usage/reconciliation")
async def reconciliation(days: int = 14, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    """The latest nightly comparisons of the ledger with Bedrock's own invocation logs."""
    _admin(user)
    rec = c.reconciler
    runs = await rec._repo.runs(max(1, min(days, 90)))
    return {"configured": rec.configured, "source": rec.source_name, "threshold": rec._threshold, "runs": [_run_json(r) for r in runs]}


@router.get("/api/usage/reconciliation/{day}")
async def reconciliation_day(day: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    _admin(user)
    try:
        d = date.fromisoformat(day)
    except ValueError as err:
        raise SdlcError("VALIDATION_FAILED", "Use a date like 2026-10-31") from err
    run = await c.reconciler._repo.run_for(d)
    if not run:
        raise SdlcError("NOT_FOUND", "No reconciliation has run for that day")
    diffs = await c.reconciler._repo.diffs(run["id"])
    return {"run": _run_json(run), "differences": [{
        "kind": x["kind"], "providerRequestId": x["provider_request_id"], "model": x["model"], "ts": x["ts"].isoformat() if x["ts"] else None, "ledgerTokens": x["ledger_tokens"],
        "awsTokens": x["aws_tokens"], "delta": x["delta"], "projectId": x["project_id"], "activity": x["activity"]} for x in diffs]}


class ReconRunBody(BaseModel):
    day: str


@router.post("/api/usage/reconciliation/run")
async def reconciliation_run(body: ReconRunBody, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    """Reconcile one completed UTC day now (replaces that day's result)."""
    _admin(user)
    rec = c.reconciler
    if not rec.configured:
        raise SdlcError("VALIDATION_FAILED", "Reconciliation is not set up: choose a log source (BEDROCK_RECON_SOURCE) and restart")
    try:
        d = date.fromisoformat(body.day)
    except ValueError as err:
        raise SdlcError("VALIDATION_FAILED", "Use a date like 2026-10-31") from err
    if d >= date.today():
        raise SdlcError("VALIDATION_FAILED", "Only completed days can be reconciled; choose yesterday or earlier")
    return await rec.run_day(d, f"manual:{user.email}")
