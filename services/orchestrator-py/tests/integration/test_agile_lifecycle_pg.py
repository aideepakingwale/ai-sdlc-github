"""End-to-end Agile lifecycle on a REAL Postgres: real Database, WorkflowService, AuthzService, GateService and
AgileService; only DynamoDB (gate state) and audit are in-memory fakes."""

import datetime as dt

import pytest

from app.domain.errors import SdlcError

from .helpers import _approve, _finish_project_stages, _stage

pytestmark = pytest.mark.asyncio


async def test_enable_scrum_replaces_the_workflow_and_creates_release_one(env):
    e = env
    with pytest.raises(SdlcError) as err:              # only the managing PM or an admin
        await e.agile.enable(e.pid, e.po, methodology="scrum")
    assert err.value.code == "FORBIDDEN"
    ov = await e.agile.enable(e.pid, e.pm, methodology="scrum", sprint_days=10, default_capacity=40)
    assert ov["enabled"] and ov["methodology"] == "scrum" and ov["settings"]["sprintDays"] == 10
    assert [r["code"] for r in ov["releases"]] == ["R-001"] and ov["currentIteration"] is None
    wf = await e.wf.view(e.pid)
    assert wf["iterative"] and [s["key"] for s in wf["stages"]] == ["vision", "runway"]   # no sprint yet
    with pytest.raises(SdlcError) as err:
        await e.agile.enable(e.pid, e.pm, methodology="kanban")
    assert err.value.code == "GATE_CONFLICT"


async def test_enable_is_refused_once_a_waterfall_stage_has_started(env):
    e = env
    await e.dynamo.put_phase_state(project_id=e.pid, phase=1, status="IN_PROGRESS", reviewer_role="PO")
    with pytest.raises(SdlcError) as err:
        await e.agile.enable(e.pid, e.pm, methodology="scrum")
    assert err.value.code == "GATE_CONFLICT" and "waterfall" in err.value.message


async def test_full_scrum_cycle_two_sprints_and_a_release(env):
    e = env
    await e.agile.enable(e.pid, e.pm, methodology="scrum")
    await _finish_project_stages(e)

    # --- sprint 1 -----------------------------------------------------------------------------
    with pytest.raises(SdlcError) as err:
        await e.agile.start_sprint(e.pid, e.dev, goal="x")                       # a developer cannot
    assert err.value.code == "FORBIDDEN"
    s1 = await e.agile.start_sprint(e.pid, e.po, goal="Checkout MVP", capacity=20,
                                    starts_on=dt.date(2026, 1, 5))
    assert s1["label"] == "S-001" and s1["status"] == "planned" and s1["endsOn"] == "2026-01-19"
    with pytest.raises(SdlcError) as err:                                        # sprints are sequential
        await e.agile.start_sprint(e.pid, e.po)
    assert "already has an open sprint" in err.value.message
    wf = await e.wf.view(e.pid)
    sprint_keys = [s["key"] for s in wf["stages"] if s.get("iterationLabel") == "S-001"]
    assert sprint_keys == ["refine@S-001", "plan@S-001", "build@S-001", "review@S-001", "retro@S-001"]
    assert (await e.pg.get_project(e.pid))["current_phase"] == (await _stage(e, "refine@S-001"))["seq"]

    # stages run strictly in order — a later stage is blocked until its dependency is approved
    plan_seq = (await _stage(e, "plan@S-001"))["seq"]
    assert (await _stage(e, "plan@S-001"))["dependsOn"] == ["refine@S-001"]

    await _approve(e, "refine@S-001")                                            # lightweight: PO alone
    await _approve(e, "plan@S-001")
    assert (await e.pg.get_open_iteration(e.pid))["status"] == "active"           # plan approved → sprint active
    await _approve(e, "build@S-001", by=e.admin)
    await _approve(e, "review@S-001")
    res = await _approve(e, "retro@S-001")
    assert res["nextPhase"] is None                                               # no "last stage": project continues
    assert (await e.pg.get_project(e.pid))["status"] == "ACTIVE"
    closed = (await e.pg.list_iterations(e.pid))[0]
    assert closed["status"] == "closed" and closed["summary"]["completed"] == 0
    assert "sprint.closed" in e.audit.events

    # --- sprint 2 gets fresh slots and waits on sprint 1 --------------------------------------
    s2 = await e.agile.start_sprint(e.pid, e.pm, goal="Payments")
    assert s2["label"] == "S-002"
    wf = await e.wf.view(e.pid)
    seqs = [s["seq"] for s in wf["stages"] if s.get("iterationLabel") == "S-002"]
    assert seqs == sorted(seqs) and min(seqs) > 8 and plan_seq not in seqs
    assert len({s["seq"] for s in wf["stages"]}) == len(wf["stages"])
    assert (await _stage(e, "refine@S-002"))["dependsOn"] == ["runway", "retro@S-001"]

    # --- release hardening only after the sprint is closed ------------------------------------
    await _approve(e, "refine@S-002"); await _approve(e, "plan@S-002")
    await _approve(e, "build@S-002", by=e.admin); await _approve(e, "review@S-002")
    await _approve(e, "retro@S-002")
    rel = await e.agile.start_release_hardening(e.pid, e.po)
    assert rel["status"] == "hardening"
    release_stage = await _stage(e, "release@R-001")
    assert release_stage["seq"] == 8 and "retro@S-002" in release_stage["dependsOn"]
    await _approve(e, "release@R-001", by=e.admin)
    ov = await e.agile.overview(e.pid, e.pm)
    assert [(r["code"], r["status"]) for r in ov["releases"]] == [("R-001", "closed"), ("R-002", "open")]
    assert len(ov["velocity"]) == 2 and "release.closed" in e.audit.events


async def test_release_hardening_preconditions(env):
    e = env
    await e.agile.enable(e.pid, e.pm, methodology="scrum")
    with pytest.raises(SdlcError) as err:
        await e.agile.start_release_hardening(e.pid, e.pm)
    assert "no completed sprint" in err.value.message
    await _finish_project_stages(e)
    await e.agile.start_sprint(e.pid, e.pm)
    with pytest.raises(SdlcError) as err:
        await e.agile.start_release_hardening(e.pid, e.pm)
    assert "Close the current sprint" in err.value.message


async def test_cancel_sprint_only_before_any_work(env):
    e = env
    await e.agile.enable(e.pid, e.pm, methodology="scrum")
    await _finish_project_stages(e)
    s = await e.agile.start_sprint(e.pid, e.pm)
    done = await e.agile.cancel_sprint(e.pid, e.pm, s["id"])
    assert done["status"] == "cancelled"
    s2 = await e.agile.start_sprint(e.pid, e.pm)                  # the slot is free again, numbering continues
    assert s2["label"] == "S-002"
    wf = await e.wf.view(e.pid)
    assert not any(st.get("iterationLabel") == "S-001" for st in wf["stages"])       # cancelled sprint is hidden
    await _approve(e, "refine@S-002")
    with pytest.raises(SdlcError) as err:
        await e.agile.cancel_sprint(e.pid, e.pm, s2["id"])
    assert "started work" in err.value.message


async def test_workflow_structure_is_frozen_after_sprints_start_but_metadata_is_not(env):
    e = env
    await e.agile.enable(e.pid, e.pm, methodology="scrum")
    await _finish_project_stages(e)
    await e.agile.start_sprint(e.pid, e.pm)
    cfg = (await e.wf.view(e.pid))["config"]
    broken = {**cfg, "stages": [s for s in cfg["stages"] if s["key"] != "release"]}   # valid, but changes the shape
    with pytest.raises(SdlcError) as err:
        await e.wf.save(e.pid, broken, e.pm)
    assert "frozen" in err.value.message
    ok = {**cfg, "stages": [{**s, "gateMode": "full"} if s["key"] == "review" else s for s in cfg["stages"]]}
    saved = await e.wf.save(e.pid, ok, e.pm)                       # a gate-mode change is allowed
    assert next(s for s in saved["stages"] if s["key"] == "review@S-001")["gateMode"] == "full"
    with pytest.raises(SdlcError) as err:                           # and the methodology cannot flip
        await e.wf.save(e.pid, {**cfg, "methodology": "waterfall"}, e.pm)


async def test_lightweight_gate_rules(env):
    e = env
    await e.agile.enable(e.pid, e.pm, methodology="scrum")
    await _finish_project_stages(e)
    await e.agile.start_sprint(e.pid, e.pm)
    st = await _stage(e, "refine@S-001")
    await e.dynamo.put_phase_state(project_id=e.pid, phase=st["seq"], status="PENDING_REVIEW", reviewer_role="PO")
    with pytest.raises(SdlcError) as err:                           # a developer is not a reviewer of this stage
        await e.gates.review(project_id=e.pid, phase=st["seq"], decision="APPROVE", comments=None, user=e.dev)
    assert err.value.code == "FORBIDDEN"
    out = await e.gates.review(project_id=e.pid, phase=st["seq"], decision="APPROVE", comments=None, user=e.po)
    assert out["status"] == "APPROVED"
    rec = next(r for r in e.audit.records if r["event"] == "gate.approved" and r["phase"] == st["seq"])
    assert rec["detail"]["gateMode"] == "lightweight"


async def test_auto_gate_approves_only_when_everything_is_safe(env):
    e = env
    await e.agile.enable(e.pid, e.pm, methodology="scrum", auto_min_score=80)
    await _finish_project_stages(e)
    await e.agile.start_sprint(e.pid, e.pm)
    await _approve(e, "refine@S-001"); await _approve(e, "plan@S-001")
    st = await _stage(e, "build@S-001")
    assert st["gateMode"] == "auto"
    await e.dynamo.put_phase_state(project_id=e.pid, phase=st["seq"], status="PENDING_REVIEW", reviewer_role="TA")

    async def score(n, severity="warning", cat="quality-score"):
        await e.pg.replace_validation_feedback(project_id=e.pid, phase=st["seq"], issues=[
            {"category": "quality-score", "severity": severity, "comment": f"Quality score {n}/100"}]
            + ([] if cat == "quality-score" else [{"category": cat, "severity": "error", "comment": "boom"}]))

    await score(70)                                                   # below the bar → human review
    assert await e.gates.try_auto_approve(e.pid, st["seq"], provider="bedrock", model="sonnet") is None
    assert "gate.auto_declined" in e.audit.events
    await score(95)
    assert await e.gates.try_auto_approve(e.pid, st["seq"], provider="mock", model="mock-1") is None   # placeholder
    await score(95, cat="mermaid")                                    # a blocking validation issue
    assert await e.gates.try_auto_approve(e.pid, st["seq"], provider="bedrock", model="s") is None
    await score(95)
    res = await e.gates.try_auto_approve(e.pid, st["seq"], provider="bedrock", model="s")
    assert res and res["status"] == "APPROVED"
    rec = next(r for r in e.audit.records if r["event"] == "gate.auto_approved")
    assert rec["detail"]["gateMode"] == "auto" and rec["detail"]["score"] == 95 and rec["human_reviewer"].startswith("auto-gate")
    assert await e.gates.try_auto_approve(e.pid, st["seq"]) is None  # not pending anymore → no double approval


async def test_non_auto_stage_is_never_auto_approved(env):
    e = env
    await e.agile.enable(e.pid, e.pm, methodology="scrum")
    await _finish_project_stages(e)
    await e.agile.start_sprint(e.pid, e.pm)
    st = await _stage(e, "refine@S-001")
    await e.dynamo.put_phase_state(project_id=e.pid, phase=st["seq"], status="PENDING_REVIEW", reviewer_role="PO")
    assert await e.gates.try_auto_approve(e.pid, st["seq"], provider="x", model="y") is None
    assert (await e.dynamo.get_phase_state(e.pid, st["seq"]))["status"] == "PENDING_REVIEW"


async def test_waterfall_project_is_untouched(env):
    e = env
    wf = await e.wf.view(e.pid)
    assert not wf["iterative"] and wf["methodology"] == "waterfall" and len(wf["stages"]) == 8
    assert (await e.agile.overview(e.pid, e.pm)) == {"enabled": False, "methodology": "waterfall",
                                                     "permissions": {"canManage": True, "canRun": True}}
    # approving the last waterfall stage still completes the project
    for s in wf["stages"][:-1]:
        await e.dynamo.put_phase_state(project_id=e.pid, phase=s["seq"], status="APPROVED", reviewer_role="PO")
    last = wf["stages"][-1]
    await e.dynamo.put_phase_state(project_id=e.pid, phase=last["seq"], status="PENDING_REVIEW", reviewer_role="DEVOPS")
    await e.gates.review(project_id=e.pid, phase=last["seq"], decision="APPROVE", comments=None, user=e.admin)
    assert (await e.pg.get_project(e.pid))["status"] == "COMPLETED"
