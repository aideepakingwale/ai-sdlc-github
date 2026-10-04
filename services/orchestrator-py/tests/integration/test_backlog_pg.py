"""Backlog, proposals and the Refine/Plan agents on a REAL Postgres, driven through the real gates."""

import asyncio
import types

import pytest

from app.agents.phase_agents import AgentDeps
from app.agile.agents import run_plan, run_refine
from app.agile.proposals import LlmPlan, LlmPlanPick, LlmRefine, LlmRefineOp
from app.config import get_settings
from app.domain.errors import SdlcError
from app.domain.models import AgentState

from .helpers import _approve, _finish_project_stages, _stage

pytestmark = pytest.mark.asyncio


async def story(e, title, *, est=3, ac=("works",), status=None, user=None):
    v = await e.backlog.create(e.pid, user or e.po, {"title": title, "estimate": est, "acceptanceCriteria": list(ac)})
    if status == "ready":
        v = await e.backlog.set_status(e.pid, user or e.po, v["key"], "ready", expected_version=v["version"])
    return v


async def started(e, **kw):
    await e.agile.enable(e.pid, e.pm, methodology="scrum", **kw)
    await _finish_project_stages(e)
    return await e.agile.start_sprint(e.pid, e.po, goal="", capacity=kw.get("default_capacity", 10))


# ------------------------------------------------------------------ authorisation + validation
async def test_who_may_read_and_write_the_backlog(env):
    e = env
    await e.agile.enable(e.pid, e.pm, methodology="scrum")
    item = await story(e, "Checkout")
    assert (await e.backlog.list(e.pid, e.dev))["items"][0]["key"] == item["key"]       # any member reads
    for who in (e.dev, e.ta):
        with pytest.raises(SdlcError) as err:
            await e.backlog.create(e.pid, who, {"title": "x"})
        assert err.value.code == "FORBIDDEN"
    with pytest.raises(SdlcError):                                                      # non-members cannot even read
        await e.backlog.list(e.pid, e.stranger)
    assert (await e.backlog.create(e.pid, e.pm, {"title": "by pm"}))["key"] == "DM-2"   # the managing PM can


async def test_input_validation(env):
    e = env
    for bad in ({"title": ""}, {"title": "x" * 201}, {"title": "ok", "estimate": 2.3}, {"title": "ok", "estimate": -1},
                {"title": "ok", "type": "saga"}, {"title": "ok", "epicKey": "DM-404"}):
        with pytest.raises(SdlcError) as err:
            await e.backlog.create(e.pid, e.po, bad)
        assert err.value.code == "VALIDATION_FAILED", bad
    story_ = await e.backlog.create(e.pid, e.po, {"title": "A story"})
    with pytest.raises(SdlcError):
        await e.backlog.create(e.pid, e.po, {"title": "child", "epicKey": story_["key"]})   # a story is not an epic


async def test_status_machine_and_definition_of_ready(env):
    e = env
    v = await e.backlog.create(e.pid, e.po, {"title": "Needs work"})
    assert v["status"] == "new" and any("acceptance" in p for p in v["problems"])
    with pytest.raises(SdlcError) as err:                              # not ready yet
        await e.backlog.set_status(e.pid, e.po, v["key"], "ready", expected_version=v["version"])
    assert "not ready" in err.value.message
    with pytest.raises(SdlcError) as err:                              # illegal jump
        await e.backlog.set_status(e.pid, e.po, v["key"], "done", expected_version=v["version"])
    assert "cannot move" in err.value.message
    v = await e.backlog.update(e.pid, e.po, v["key"], {"acceptanceCriteria": ["a"], "estimate": 5}, expected_version=v["version"])
    v = await e.backlog.set_status(e.pid, e.po, v["key"], "ready", expected_version=v["version"])
    assert v["status"] == "ready" and v["problems"] == []
    d = await e.backlog.set_status(e.pid, e.po, v["key"], "dropped", expected_version=v["version"])
    with pytest.raises(SdlcError):
        await e.backlog.update(e.pid, e.po, d["key"], {"title": "revive"}, expected_version=d["version"])
    assert (await e.backlog.set_status(e.pid, e.po, d["key"], "new", expected_version=d["version"]))["status"] == "new"


async def test_lost_update_is_rejected(env):
    e = env
    v = await e.backlog.create(e.pid, e.po, {"title": "Shared"})
    await e.backlog.update(e.pid, e.po, v["key"], {"title": "Mine"}, expected_version=v["version"])
    with pytest.raises(SdlcError) as err:
        await e.backlog.update(e.pid, e.pm, v["key"], {"title": "Theirs"}, expected_version=v["version"])
    assert err.value.code == "GATE_CONFLICT" and "someone else" in err.value.message
    assert (await e.backlog.get(e.pid, e.po, v["key"]))["title"] == "Mine"


async def test_ranking_survives_many_inserts_between_the_same_neighbours(env):
    e = env
    a = await e.backlog.create(e.pid, e.po, {"title": "A"}); b = await e.backlog.create(e.pid, e.po, {"title": "B"})
    moved = []
    for i in range(45):                                    # squeeze item after item between A and B → forces a rebalance
        x = await e.backlog.create(e.pid, e.po, {"title": f"X{i}"})
        await e.backlog.move(e.pid, e.po, x["key"], after=a["key"])
        moved.append(x["key"])
    items = (await e.backlog.list(e.pid, e.po))["items"]
    order = [i["key"] for i in items]
    assert order[0] == a["key"] and order[-1] == b["key"] and order[1:-1] == list(reversed(moved))
    ranks = [i["rank"] for i in items]
    assert ranks == sorted(ranks) and len(set(ranks)) == len(ranks)            # strictly ordered, no ties
    await e.backlog.move(e.pid, e.po, b["key"], before=a["key"])
    assert (await e.backlog.list(e.pid, e.po))["items"][0]["key"] == b["key"]
    with pytest.raises(SdlcError):
        await e.backlog.move(e.pid, e.po, b["key"], before=a["key"], after=a["key"])


# ------------------------------------------------------------------ sprint commitment
async def test_capacity_is_enforced_and_overcommit_is_explicit_and_audited(env):
    e = env
    await started(e, default_capacity=10)
    a = await story(e, "A", est=5, status="ready"); b = await story(e, "B", est=5, status="ready")
    c = await story(e, "C", est=3, status="ready")
    await e.backlog.add_to_sprint(e.pid, e.po, a["key"]); await e.backlog.add_to_sprint(e.pid, e.po, b["key"])
    with pytest.raises(SdlcError) as err:
        await e.backlog.add_to_sprint(e.pid, e.po, c["key"])
    assert "Confirm to overcommit" in err.value.message
    await e.backlog.add_to_sprint(e.pid, e.po, c["key"], force=True)
    rec = [r for r in e.audit.records if r["event"] == "sprint.item_added"][-1]
    assert rec["detail"]["overcommit"] is True and rec["detail"]["points"] == 13
    out = await e.backlog.remove_from_sprint(e.pid, e.po, c["key"])
    assert out["status"] == "ready" and out["iterationId"] is None
    with pytest.raises(SdlcError):
        await e.backlog.add_to_sprint(e.pid, e.po, await _unready(e))     # only ready items


async def _unready(e):
    return (await e.backlog.create(e.pid, e.po, {"title": "Not ready"}))["key"]


async def test_wip_limit(env):
    e = env
    await e.agile.enable(e.pid, e.pm, methodology="kanban", wip_limit=2)
    items = [await story(e, f"W{i}", est=1, status="ready") for i in range(3)]
    # kanban sprints are active immediately; commit and start the first two
    await _finish_project_stages(e)
    await e.agile.start_sprint(e.pid, e.po, capacity=50)
    for it in items:
        await e.backlog.add_to_sprint(e.pid, e.po, it["key"])
    await e.backlog.set_status(e.pid, e.dev, items[0]["key"], "in_progress", expected_version=None)
    await e.backlog.set_status(e.pid, e.dev, items[1]["key"], "in_progress", expected_version=None)
    with pytest.raises(SdlcError) as err:
        await e.backlog.set_status(e.pid, e.dev, items[2]["key"], "in_progress", expected_version=None)
    assert "WIP limit reached (2)" in err.value.message
    with pytest.raises(SdlcError):                       # only the PO may accept work as done
        await e.backlog.set_status(e.pid, e.dev, items[0]["key"], "done", expected_version=None)
    await e.backlog.set_status(e.pid, e.po, items[0]["key"], "done", expected_version=None)
    await e.backlog.set_status(e.pid, e.dev, items[2]["key"], "in_progress", expected_version=None)   # room again


async def test_closing_a_sprint_records_velocity_and_returns_unfinished_work(env):
    e = env
    s = await started(e, default_capacity=20)
    a = await story(e, "A", est=5, status="ready"); b = await story(e, "B", est=8, status="ready")
    for it in (a, b):
        await e.backlog.add_to_sprint(e.pid, e.po, it["key"])
    await e.backlog.set_status(e.pid, e.dev, a["key"], "in_progress", expected_version=None)
    await e.backlog.set_status(e.pid, e.po, a["key"], "done", expected_version=None)
    summary = await e.agile.close_sprint(e.pid, s["id"], "po@t.local")
    assert summary["status"] == "closed"
    assert summary["summary"]["velocity"] == 5 and summary["summary"]["completed"] == 1
    assert summary["summary"]["carried"] == 1 and summary["summary"]["completionRate"] == 0.5
    assert (await e.backlog.get(e.pid, e.po, b["key"]))["status"] == "ready"       # back in the backlog
    assert (await e.backlog.get(e.pid, e.po, a["key"]))["status"] == "done"
    again = await e.agile.close_sprint(e.pid, s["id"], "po@t.local")                  # idempotent
    assert again["summary"] == summary["summary"]
    ov = await e.agile.overview(e.pid, e.po)
    assert ov["velocity"] == [{"sprint": "S-001", "points": 5.0, "completed": 1, "release": "R-001"}] and ov["averageVelocity"] == 5.0


# ------------------------------------------------------------------ agents + proposals through real gates
class FakeLlm:
    telemetry = None

    def __init__(self, out=None, fail=False):
        self.out, self.fail, self.calls = out, fail, []

    async def generate_json(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise RuntimeError("model unavailable")
        return self.out, types.SimpleNamespace(provider="bedrock", model="sonnet",
                                               content="{}", usage={"promptTokens": 5, "completionTokens": 9})


class MemContent:
    mode = "filesystem"

    def __init__(self):
        self.store = {}

    async def put(self, k, v):
        self.store[k] = v

    async def get(self, k):
        return self.store.get(k)


def deps_for(e, llm):
    async def _noop(*_a, **_k):
        return None

    return AgentDeps(llm=llm, mcp=None, db=e.pg, audit=e.audit,
                     rag=types.SimpleNamespace(index_artifact=_noop, retrieve=_noop),
                     content=MemContent(), monitor=None, settings=get_settings(), proposals=e.proposals)


async def run_agent(e, runner, key, llm, instruction="go"):
    st = await _stage(e, key)
    state = AgentState(
        project_id=e.pid, session_id="s1", current_phase=st["seq"], stage_template=7, stage_name=st["name"],
        stage_reviewer=st["reviewerRole"], user_input=instruction, agile_role=st["agileRole"],
        iteration_id=st["iterationId"], custom_persona=st["persona"], custom_outputs=list(st["outputs"]))
    return await runner(deps_for(e, llm), state, lambda _e: None)


async def test_refine_agent_proposes_and_nothing_changes_until_approval(env):
    e = env
    await started(e)
    llm = FakeLlm(LlmRefine(summary="two stories", ops=[
        LlmRefineOp(ref="s1", title="Pay by card", acceptanceCriteria=["card accepted"], estimate=4, epic="e1"),
        LlmRefineOp(ref="e1", type="epic", title="Payments"),
        LlmRefineOp(ref="s2", title="Refund", estimate=2)]))
    res = await run_agent(e, run_refine, "refine@S-001", llm)
    assert res.gate_status == "PENDING_REVIEW" and len(res.new_artifacts) == 1
    assert (await e.backlog.list(e.pid, e.po))["items"] == []                    # proposal only
    st = await _stage(e, "refine@S-001")
    prop = await e.proposals.latest(e.pid, e.po, st["seq"], "refine")
    assert prop["status"] == "proposed" and [o["ref"] for o in prop["payload"]["ops"]] == ["e1", "s1", "s2"]
    assert any("no acceptance criteria" in w for w in prop["warnings"])
    assert "Pay by card" in res.new_artifacts[0].content and "no acceptance criteria" in res.new_artifacts[0].content

    await _approve(e, "refine@S-001")                                            # lightweight gate by the PO
    items = (await e.backlog.list(e.pid, e.po))["items"]
    by = {i["title"]: i for i in items}
    assert set(by) == {"Payments", "Pay by card", "Refund"}
    assert by["Pay by card"]["epicKey"] == by["Payments"]["key"] and by["Pay by card"]["estimate"] == 3  # 4 snapped to 3
    assert by["Pay by card"]["status"] == "refined" and by["Refund"]["status"] == "new"
    assert (await e.proposals.latest(e.pid, e.po, st["seq"], "refine"))["status"] == "applied"
    n = len(items)
    await e.agile.on_stage_approved(e.pid, st["seq"], "po@t.local")               # reconcile / replay → no duplicates
    assert len((await e.backlog.list(e.pid, e.po))["items"]) == n


async def test_refine_still_completes_when_the_model_fails(env):
    e = env
    await started(e)
    res = await run_agent(e, run_refine, "refine@S-001", FakeLlm(fail=True))
    assert res.gate_status == "PENDING_REVIEW"
    st = await _stage(e, "refine@S-001")
    prop = await e.proposals.latest(e.pid, e.po, st["seq"], "refine")
    assert prop["payload"]["ops"] == [] and "could not be produced" in prop["warnings"][0]


async def test_a_new_run_supersedes_the_old_proposal(env):
    e = env
    await started(e)
    await run_agent(e, run_refine, "refine@S-001", FakeLlm(LlmRefine(ops=[LlmRefineOp(title="First idea")])))
    await run_agent(e, run_refine, "refine@S-001", FakeLlm(LlmRefine(ops=[LlmRefineOp(title="Second idea")])))
    await _approve(e, "refine@S-001")
    assert [i["title"] for i in (await e.backlog.list(e.pid, e.po))["items"]] == ["Second idea"]


async def test_plan_agent_commits_only_valid_ready_items_on_approval(env):
    e = env
    await started(e, default_capacity=8)
    a = await story(e, "A", est=5, status="ready"); b = await story(e, "B", est=3, status="ready")
    c = await story(e, "C", est=5, status="ready"); d = await story(e, "D", est=2)          # D is not ready
    await _approve(e, "refine@S-001")
    llm = FakeLlm(LlmPlan(goal="Ship checkout", picks=[LlmPlanPick(key=k, reason="value") for k in
                                                       (a["key"], c["key"], b["key"], d["key"], "DM-404")]))
    res = await run_agent(e, run_plan, "plan@S-001", llm)
    assert "Ship checkout" in res.new_artifacts[0].content
    st = await _stage(e, "plan@S-001")
    prop = await e.proposals.latest(e.pid, e.po, st["seq"], "plan")
    assert [i["key"] for i in prop["payload"]["items"]] == [a["key"], b["key"]] and prop["payload"]["points"] == 8
    # someone drops B before the plan is approved: the plan is re-validated, not blindly applied
    await e.backlog.set_status(e.pid, e.po, b["key"], "dropped", expected_version=None)
    await _approve(e, "plan@S-001")
    sprint = (await e.pg.list_iterations(e.pid))[0]
    assert sprint["status"] == "active" and sprint["goal"] == "Ship checkout"
    in_sprint = (await e.backlog.list(e.pid, e.po, iteration_id=sprint["id"]))["items"]
    assert [i["key"] for i in in_sprint] == [a["key"]] and in_sprint[0]["status"] == "in_sprint"
    assert (await e.backlog.get(e.pid, e.po, c["key"]))["status"] == "ready"
    rec = [r for r in e.audit.records if r["event"] == "plan.committed"][0]
    assert rec["detail"]["assigned"] == [a["key"]]


async def test_plan_falls_back_to_rank_order_when_the_model_is_useless(env):
    e = env
    await started(e, default_capacity=6)
    first = await story(e, "First", est=3, status="ready"); second = await story(e, "Second", est=3, status="ready")
    await story(e, "Third", est=3, status="ready")
    for llm in (FakeLlm(fail=True), FakeLlm(LlmPlan(picks=[LlmPlanPick(key="DM-999")]))):
        await run_agent(e, run_plan, "plan@S-001", llm)
        st = await _stage(e, "plan@S-001")
        prop = await e.proposals.latest(e.pid, e.po, st["seq"], "plan")
        assert [i["key"] for i in prop["payload"]["items"]] == [first["key"], second["key"]]
        assert prop["warnings"]


async def test_proposal_can_be_edited_by_the_po_and_stays_enforced(env):
    e = env
    await started(e, default_capacity=5)
    a = await story(e, "A", est=3, status="ready"); b = await story(e, "B", est=3, status="ready")
    await run_agent(e, run_plan, "plan@S-001", FakeLlm(LlmPlan(picks=[LlmPlanPick(key=a["key"])])))
    st = await _stage(e, "plan@S-001")
    prop = await e.proposals.latest(e.pid, e.po, st["seq"], "plan")
    with pytest.raises(SdlcError):                                                   # a developer cannot edit it
        await e.proposals.edit(e.pid, e.dev, prop["id"], prop["payload"], prop["version"])
    both = {**prop["payload"], "items": [{"key": a["key"]}, {"key": b["key"]}]}
    edited = await e.proposals.edit(e.pid, e.po, prop["id"], both, prop["version"])
    assert [i["key"] for i in edited["payload"]["items"]] == [a["key"]]               # B would exceed capacity 5
    assert any("exceed the capacity" in w for w in edited["warnings"])
    with pytest.raises(SdlcError) as err:                                             # stale version
        await e.proposals.edit(e.pid, e.po, prop["id"], both, prop["version"])
    assert err.value.code == "GATE_CONFLICT"


async def test_concurrent_apply_happens_exactly_once(env):
    e = env
    await started(e)
    await run_agent(e, run_refine, "refine@S-001", FakeLlm(LlmRefine(ops=[LlmRefineOp(title="Once", acceptanceCriteria=["a"])])))
    st = await _stage(e, "refine@S-001")
    row = await e.pg.latest_proposal(e.pid, st["seq"], "refine")
    ops = row["payload"]["ops"]
    results = await asyncio.gather(*[
        e.pg.apply_refine(project_id=e.pid, proposal_id=row["id"], ops=ops, actor_id=None) for _ in range(6)])
    assert sum(1 for r in results if r is not None) == 1
    assert [i["title"] for i in (await e.backlog.list(e.pid, e.po))["items"]] == ["Once"]


async def test_jira_style_import_cannot_collide_with_keys(env):
    e = env
    await e.pg.insert_backlog_item(project_id=e.pid, created_by=None, title="From Jira", jira_key="SHOP-7")
    row = await e.pg.get_backlog_by_jira(e.pid, "SHOP-7")
    assert row["item_key"] == "DM-1" and row["status"] == "new"
