"""Shared environment + helpers for the Agile integration tests (real Postgres)."""

import pytest

from app.agile.backlog import BacklogService
from app.agile.proposal_service import ProposalService
from app.agile.service import AgileService
from app.domain.models import UserPublic
from app.services.authz import AuthzService
from app.services.gates import GateService
from app.services.workflow import WorkflowService

from ..conftest import FakeAudit, FakeDynamo


class Env:
    pass


async def make_user(pg, role, name):
    uid = f"u-{name}"
    await pg.pool.execute(
        "INSERT INTO users (id, email, display_name, role, password_hash) VALUES ($1,$2,$3,$4,'x') "
        "ON CONFLICT (id) DO NOTHING", uid, f"{name}@t.local", name, role)
    return UserPublic(id=uid, email=f"{name}@t.local", displayName=name, role=role)


async def build_env(pg) -> Env:
    e = Env()
    e.pg, e.dynamo, e.audit = pg, FakeDynamo(), FakeAudit()
    e.pm = await make_user(pg, "PROJECT_MANAGER", "pm")
    e.po = await make_user(pg, "PO", "po")
    e.dev = await make_user(pg, "DEV", "dev")
    e.ta = await make_user(pg, "TA", "ta")
    e.admin = await make_user(pg, "SUPER_ADMIN", "admin")
    e.stranger = await make_user(pg, "DEV", "stranger")
    e.pid = "proj-agile"
    await pg.pool.execute("INSERT INTO projects (id, name, created_by) VALUES ($1,'Shop',$2)", e.pid, e.pm.id)
    for u, role in ((e.po, "PO"), (e.dev, "DEV"), (e.ta, "TA")):
        await pg.add_member(project_id=e.pid, user_id=u.id, role=role, added_by=e.pm.id)
    e.authz = AuthzService(pg)
    e.wf = WorkflowService(pg, e.dynamo, e.audit)
    e.agile = AgileService(pg, e.dynamo, e.wf, e.audit, e.authz)
    e.backlog = BacklogService(pg, e.audit, e.authz, e.agile)
    e.proposals = ProposalService(pg, e.audit, e.authz, e.agile)
    e.agile.on_approved("refine", e.proposals.apply_refine_hook)
    e.agile.on_approved("plan", e.proposals.apply_plan_hook)

    async def regen(*_a):
        return None

    e.gates = GateService(pg, e.dynamo, e.audit, e.authz, e.wf, regen, None, e.agile)
    return e


async def _stage(e, key_prefix):
    wf = await e.wf.view(e.pid)
    return next(s for s in wf["stages"] if s["key"].startswith(key_prefix))


async def _approve(e, key, by=None):
    """Drive one stage: generation done (PENDING_REVIEW) → approved through the REAL gate service."""
    st = await _stage(e, key)
    await e.dynamo.put_phase_state(project_id=e.pid, phase=st["seq"], status="PENDING_REVIEW",
                                   reviewer_role=st["reviewerRole"])
    return await e.gates.review(project_id=e.pid, phase=st["seq"], decision="APPROVE", comments=None,
                                user=by or e.po)


async def _finish_project_stages(e):
    for key in ("vision", "runway"):
        await _approve(e, key, by=e.admin)             # full gates: override path
