"""Custom agents and skills: the super-admin's library, a project's own agents and skills, the audit and approval flow, stage attachments,
who may build or approve, and the guardrails. Rules of visibility and permission live in services/agent_defs.py; these routes only carry them."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..domain.models import UserPublic
from .deps import Container, current_user, get_container

router = APIRouter()


class CreateBody(BaseModel):
    kind: str
    name: str
    scope: str = "project"
    projectId: str | None = None
    body: dict[str, Any] | None = None


class ForkBody(BaseModel):
    sourceKind: str = "def"          # core | def
    sourceId: str
    kind: str = "agent"
    scope: str = "project"
    projectId: str | None = None
    name: str | None = None


class DraftBody(BaseModel):
    name: str | None = None
    body: dict[str, Any]


class AckBody(BaseModel):
    ack: bool = True


class DecisionBody(BaseModel):
    decision: str
    comment: str = ""


class OpenBody(BaseModel):
    open: bool


class TestBody(BaseModel):
    inputs: dict[str, Any] = {}
    version: int | None = None


class CaseBody(BaseModel):
    name: str
    inputs: dict[str, Any] = {}


class DraftTextBody(BaseModel):
    text: str


class CompareBody(BaseModel):
    versionA: int | None = None
    versionB: int | None = None


class LimitBody(BaseModel):
    monthlyTokens: int | None = None


class StageItem(BaseModel):
    defId: str
    pinnedVersion: int | None = None
    runs: str = "always"
    condition: str = ""
    roles: list[str] = []


class StageBody(BaseModel):
    items: list[StageItem]


class GrantBody(BaseModel):
    userId: str
    canEdit: bool = False
    canApprove: bool = False


class SeverityBody(BaseModel):
    severity: str


class RunOnRequestBody(BaseModel):
    brief: str = ""
    inputs: dict[str, Any] = {}


# ------------------------------------------------------------------ library (super-admin)
@router.get("/api/agent-library")
async def library(kind: str = "agent", user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.agent_defs.library(user, kind)


@router.get("/api/agent-library/core/{kind}/{core_id}")
async def core_detail(kind: str, core_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return c.agent_defs.core_detail(user, kind, core_id)


@router.get("/api/agent-library/approvals")
async def org_approvals(user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return {"pending": await c.agent_defs.pending(user, None)}


@router.get("/api/agent-guardrails")
async def guardrails(user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return {"guardrails": await c.agent_defs.guardrails(), "canEdit": user.role == "SUPER_ADMIN"}


@router.put("/api/agent-guardrails/{gid}")
async def set_guardrail(gid: str, body: SeverityBody, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return {"guardrails": await c.agent_defs.set_guardrail(user, gid, body.severity), "canEdit": True}


# ------------------------------------------------------------------ definitions
@router.post("/api/agent-defs")
async def create_def(body: CreateBody, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.agent_defs.create(user, kind=body.kind, name=body.name, scope=body.scope, project_id=body.projectId, body=body.body)


@router.post("/api/agent-defs/fork")
async def fork_def(body: ForkBody, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.agent_defs.fork(user, source_kind=body.sourceKind, source_id=body.sourceId, kind=body.kind, scope=body.scope, project_id=body.projectId, name=body.name)


@router.get("/api/agent-defs/{def_id}")
async def get_def(def_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.agent_defs.detail(user, def_id)


@router.put("/api/agent-defs/{def_id}/draft")
async def save_draft(def_id: str, body: DraftBody, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.agent_defs.save_draft(user, def_id, name=body.name, body=body.body)


@router.post("/api/agent-defs/{def_id}/audit")
async def run_audit(def_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    report = await c.agent_defs.run_audit(user, def_id)
    return {"audit": report, **await c.agent_defs.detail(user, def_id)}


@router.post("/api/agent-defs/{def_id}/audit/fix/{finding_id}")
async def apply_fix(def_id: str, finding_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.agent_defs.apply_fix(user, def_id, finding_id)


@router.post("/api/agent-defs/{def_id}/audit/ack")
async def acknowledge(def_id: str, body: AckBody, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.agent_defs.acknowledge(user, def_id, body.ack)


@router.post("/api/agent-defs/{def_id}/submit")
async def submit(def_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.agent_defs.submit(user, def_id)


@router.post("/api/agent-defs/{def_id}/withdraw")
async def withdraw(def_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.agent_defs.withdraw(user, def_id)


@router.post("/api/agent-defs/{def_id}/decision")
async def decide(def_id: str, body: DecisionBody, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.agent_defs.decide(user, def_id, body.decision, body.comment)


@router.put("/api/agent-defs/{def_id}/open")
async def set_open(def_id: str, body: OpenBody, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.agent_defs.set_open(user, def_id, body.open)


@router.post("/api/agent-defs/{def_id}/retire")
async def retire(def_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.agent_defs.retire(user, def_id)


@router.delete("/api/agent-defs/{def_id}")
async def delete_def(def_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.agent_defs.delete_unpublished(user, def_id)


@router.post("/api/agent-defs/{def_id}/test")
async def test_run(def_id: str, body: TestBody, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.agent_defs.run_test(user, def_id, body.inputs, body.version)


@router.post("/api/agent-defs/{def_id}/draft-from-text")
async def draft_from_text(def_id: str, body: DraftTextBody, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.agent_defs.draft_from_text(user, def_id, body.text)


@router.post("/api/agent-defs/{def_id}/compare")
async def compare(def_id: str, body: CompareBody, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.agent_defs.run_compare(user, def_id, body.versionA, body.versionB)


@router.get("/api/agent-defs/{def_id}/usage")
async def def_usage(def_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.agent_defs.def_usage(user, def_id)


@router.post("/api/agent-defs/{def_id}/cases")
async def add_case(def_id: str, body: CaseBody, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.agent_defs.add_case(user, def_id, body.name, body.inputs)


@router.delete("/api/agent-defs/{def_id}/cases/{case_id}")
async def delete_case(def_id: str, case_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.agent_defs.delete_case(user, def_id, case_id)


# ------------------------------------------------------------------ a project's agents and skills
@router.get("/api/projects/{project_id}/agents")
async def project_agents(project_id: str, kind: str | None = None, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.agent_defs.for_project(user, project_id, kind)


@router.get("/api/projects/{project_id}/agent-approvals")
async def project_approvals(project_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return {"pending": await c.agent_defs.pending(user, project_id)}


@router.get("/api/projects/{project_id}/agent-grants")
async def get_grants(project_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.agent_defs.grants(user, project_id)


@router.put("/api/projects/{project_id}/agent-grants")
async def put_grant(project_id: str, body: GrantBody, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.agent_defs.set_grant(user, project_id, body.userId, body.canEdit, body.canApprove)


@router.get("/api/projects/{project_id}/stages/{stage_key}/agents")
async def stage_agents(project_id: str, stage_key: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.agent_defs.stage_items(user, project_id, stage_key)


@router.put("/api/projects/{project_id}/stages/{stage_key}/agents")
async def set_stage_agents(project_id: str, stage_key: str, body: StageBody, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.agent_defs.set_stage_items(user, project_id, stage_key, [i.model_dump() for i in body.items])


@router.get("/api/projects/{project_id}/agent-usage")
async def project_usage(project_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.agent_defs.project_usage(user, project_id)


@router.put("/api/projects/{project_id}/agent-limits")
async def put_limit(project_id: str, body: LimitBody, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.agent_defs.set_limit(user, project_id, body.monthlyTokens)
