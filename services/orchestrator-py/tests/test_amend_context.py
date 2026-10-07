"""Amending a stage extends what it already knew: the previous version goes to the model and the
history (clarifications, every amendment) is itemised for the context graph."""
from types import SimpleNamespace

import pytest

from app.services.chat import ChatService
from app.services.context_manifest import build_manifest
from app.services.gates import _stored_overrides

OVERLAY = ("Design the integration.\n\n## Clarifications (confirmed by the reviewer)\n- Which cloud?\n  → AWS\n\n"
           "Reviewer's requested changes (a@x.com):\nAdd a retry section.\n\n"
           "Reviewer's requested changes (b@x.com):\nName the queues.")


def _svc(status: str):
    class Db:
        async def get_stage_plan(self, pid, phase):
            return None

        async def list_artefacts(self, pid):
            return [{"id": "r1", "phase": 3, "type": "LLD", "title": "LLD v1", "content": "# LLD\nbody " * 50, "storage_key": None},
                    {"id": "r2", "phase": 4, "type": "OTHER", "title": "other stage", "content": "x", "storage_key": None}]

    class Dynamo:
        async def get_phase_state(self, pid, phase):
            return {"status": status}

    svc = ChatService.__new__(ChatService)
    svc._db, svc._dynamo, svc._settings = Db(), Dynamo(), SimpleNamespace(REVISION_CONTEXT_CHARS=60_000)
    svc._deps = SimpleNamespace(content=SimpleNamespace(get=None))
    return svc


@pytest.mark.asyncio
async def test_an_amended_stage_gets_its_previous_version_and_an_itemised_history():
    items: list = []
    block = await _svc("AMEND_REQUESTED")._revision_context("p", 3, OVERLAY, items)
    assert "AMENDED" in block and "### Previous version — LLD: LLD v1" in block and "other stage" not in block
    labels = [i["label"] for i in items]
    assert "Previous version - LLD v1" in labels and "Clarification answers" in labels
    assert "Amendment 1 - a@x.com" in labels and "Amendment 2 - b@x.com" in labels


@pytest.mark.asyncio
async def test_a_stage_that_is_not_being_amended_adds_nothing():
    items: list = []
    assert await _svc("PENDING_REVIEW")._revision_context("p", 3, OVERLAY, items) == "" and items == []


def test_history_gets_its_own_layer_in_the_manifest():
    m = build_manifest(
        mode="preview", phase=3, stage={"name": "LLD", "template": 3, "outputs": ["LLD"], "persona": "TA"}, project={},
        overlay={"promptOverlay": OVERLAY}, context_artifacts=[], snippets=[], canon_block="", formworks=[],
        attached=[{"kind": "revision", "id": "r1", "label": "Previous version - LLD v1", "chars": 10},
                  {"kind": "amendment", "id": "amend-1", "label": "Amendment 1", "chars": 5},
                  {"kind": "attachment", "id": "a1", "label": "hld.doc", "chars": 7}],
        traits={}, has_codebase=False)
    by = {layer["id"]: [i["label"] for i in layer["items"]] for layer in m["layers"]}
    assert by["revision"] == ["Previous version - LLD v1", "Amendment 1"] and by["attached"] == ["hld.doc"]


def test_seeding_an_amend_keeps_the_per_step_model_choices():
    assert _stored_overrides({"step_overrides": '{"s1": {"model": "m"}}'}) == {"s1": {"model": "m"}}
    assert _stored_overrides(None) == {} and _stored_overrides({"step_overrides": "bad"}) == {}


# ----------------------------------------------------------- the amend / fresh choice
class _Plans:
    """A tiny in-memory stage_plans row + the few DB calls the choice touches."""
    def __init__(self, overlay, base, mode="pending"):
        self.row = {"prompt_overlay": overlay, "amend_base": base, "amend_mode": mode, "origin": "amend",
                    "referenced_artifact_ids": [], "attachment_ids": [], "formwork_ids": [], "step_overrides": {}}
        self.calls = []

    async def get_stage_plan(self, pid, phase):
        return dict(self.row)

    async def upsert_stage_plan(self, **kw):
        self.row["prompt_overlay"] = kw["prompt_overlay"]

    async def set_stage_amend(self, pid, phase, mode, base=None, *, keep_base=False):
        self.row["amend_mode"] = mode
        if not keep_base:
            self.row["amend_base"] = base

    async def set_stage_clarification(self, *a):
        self.calls.append("clarification")

    async def set_stage_plan_intel(self, *a):
        self.calls.append("intel")

    async def delete_stage_traits(self, *a):
        self.calls.append("traits")

    async def get_session(self, pid):
        return None


def _choice_svc(plans):
    svc = ChatService.__new__(ChatService)
    svc._db = plans
    svc._audit = SimpleNamespace(record=lambda **kw: None)
    svc._redis = SimpleNamespace(delete=lambda *a: _noop(), exists=lambda *a: _noop(False))

    async def stage_for(pid, phase):
        return None, {"key": "lld", "name": "LLD"}

    async def can_write(*a):
        return True

    async def not_generating(*a):
        return None

    svc._stage_for, svc._can_write_stage, svc.assert_not_generating = stage_for, can_write, not_generating
    svc._stage_writers = lambda stage: ["TA"]
    return svc


async def _noop(v=None):
    return v


BASE = "Design the integration.\n\n## Clarifications (confirmed by the reviewer)\n- Cloud?\n  → AWS"
AMEND = "Reviewer's requested changes (a@x.com):\nAdd a retry section."


@pytest.mark.asyncio
async def test_choosing_amend_keeps_the_earlier_instructions_and_answers():
    plans = _Plans(f"{BASE}\n\n{AMEND}", BASE)
    await _choice_svc(plans).set_amend_mode(project_id="p", phase=3, user=SimpleNamespace(id="u", email="e"), mode="amend")
    assert plans.row["amend_mode"] == "amend" and plans.row["prompt_overlay"].startswith(BASE) and "retry" in plans.row["prompt_overlay"]
    assert plans.calls == []                                   # nothing discarded


@pytest.mark.asyncio
async def test_choosing_fresh_starts_from_the_new_instructions_alone_and_can_be_switched_back():
    plans = _Plans(f"{BASE}\n\n{AMEND}", BASE)
    svc = _choice_svc(plans)
    user = SimpleNamespace(id="u", email="e")
    await svc.set_amend_mode(project_id="p", phase=3, user=user, mode="fresh")
    assert plans.row["prompt_overlay"] == AMEND and "AWS" not in plans.row["prompt_overlay"]
    assert set(plans.calls) == {"clarification", "intel", "traits"}      # earlier answers / judgement discarded
    assert plans.row["amend_base"] == BASE                                 # ...but the base is kept for a switch back
    await svc.set_amend_mode(project_id="p", phase=3, user=user, mode="amend")
    assert plans.row["prompt_overlay"].startswith(BASE) and "retry" in plans.row["prompt_overlay"]


@pytest.mark.asyncio
async def test_a_stage_that_is_not_being_amended_has_no_choice_to_make():
    from app.domain.errors import SdlcError
    plans = _Plans("x", "", mode=None)
    with pytest.raises(SdlcError):
        await _choice_svc(plans).set_amend_mode(project_id="p", phase=3, user=SimpleNamespace(id="u", email="e"), mode="fresh")


@pytest.mark.asyncio
async def test_planning_waits_for_the_choice():
    from app.domain.errors import SdlcError
    plans = _Plans("x", "b", mode="pending")
    svc = _choice_svc(plans)

    async def status(*a, **k):
        return {"generating": False, "building": False, "planned": True, "stale": False}

    async def access(*a):
        return None

    svc._plan_status, svc._authz = status, SimpleNamespace(assert_project_access=access)
    with pytest.raises(SdlcError, match="how to re-plan"):
        await svc.assert_plan_ready("p", 3, SimpleNamespace())


# ----------------------------------------------------------- security review skill
@pytest.mark.asyncio
async def test_security_review_reads_the_projects_artifacts_and_reports_what_it_reviewed():
    from app.services.skills import SKILLS, SkillContext, _security_review

    assert any(s.id == "security_review" and s.phase is None and "TA" in s.roles and "PO" not in s.roles for s in SKILLS)

    class Db:
        async def list_artefacts(self, pid):
            return [{"type": "HLD", "title": "HLD v1", "content": "# HLD\nuses S3", "storage_key": None},
                    {"type": "CDK", "title": "stack", "content": "new Bucket()", "storage_key": None},
                    {"type": "EPIC", "title": "ignored", "content": "x", "storage_key": None}]

    seen = {}

    class Llm:
        async def generate(self, **kw):
            seen["user"] = kw["messages"][-1]["content"]
            return SimpleNamespace(content="## Summary\nMEDIUM", provider="p", tier="frontier", model="m")

    ctx = SkillContext(project_id="p", phase=3, tech_stack="Python", user=None, user_input="data protection",
                       deps=SimpleNamespace(db=Db(), llm=Llm(), content=SimpleNamespace(get=None)))
    out = await _security_review(ctx)
    assert out["meta"]["artifactsReviewed"] == 2 and "[HLD]" in seen["user"] and "[CDK]" in seen["user"] and "ignored" not in seen["user"]
    assert "Focus the review on: data protection" in seen["user"]

    ctx.deps.db.list_artefacts = lambda pid: _noop([])
    from app.domain.errors import SdlcError
    with pytest.raises(SdlcError, match="nothing to review"):
        await _security_review(ctx)
