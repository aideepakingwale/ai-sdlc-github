"""Regression tests for the independent review of parallel releases (each one reproduced a real defect)."""

import asyncio

import pytest

from app.agile.release_setup import ReleaseSetupService
from app.agile.carry import CarryService
from app.agile.rules import level_peers
from app.agile.templates import preset_stages, scrum_workflow
from app.domain.errors import SdlcError

from .helpers import _approve, _finish_project_stages, _stage

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def rx(env, tmp_path):
    from app.agile.index_service import IndexService
    from app.services.content_store import FilesystemContentStore
    e = env
    e.index = IndexService(e.pg, FilesystemContentStore(str(tmp_path)), e.audit, None, e.wf)
    e.carry = CarryService(e.pg, e.index, e.audit)
    e.setup = ReleaseSetupService(e.pg, e.audit, e.agile, e.backlog, e.carry, e.index)
    await e.agile.enable(e.pid, e.pm, methodology="scrum")
    await _finish_project_stages(e)
    e.r1 = (await e.pg.list_releases(e.pid))[0]
    return e


async def _item(e, title, *, release=None, est=3):
    d = {"title": title, "estimate": est, "acceptanceCriteria": ["ok"]}
    if release:
        d["releaseId"] = release
    v = await e.backlog.create(e.pid, e.po, d)
    return await e.backlog.set_status(e.pid, e.po, v["key"], "ready", expected_version=v["version"])


async def test_stages_run_together_only_within_their_own_release(rx):
    e = rx
    r2 = await e.agile.create_release(e.pid, e.po, name="Other")
    await e.agile.start_sprint(e.pid, e.po, release_id=e.r1["id"])
    await e.agile.start_sprint(e.pid, e.po, release_id=r2["id"])
    wf = await e.wf.view(e.pid)
    a, b = (next(s for s in wf["stages"] if s["key"] == k) for k in ("refine@S-001", "refine@S-002"))
    assert a["level"] == b["level"]                                       # the same dependency level…
    assert level_peers(wf, a["seq"]) == [a["seq"]] and level_peers(wf, b["seq"]) == [b["seq"]]   # …different lanes
    assert level_peers(wf, 1) == [1]                                      # foundation stays its own lane


async def test_waterfall_levels_are_unchanged_by_lane_scoping(env):
    wf = await env.wf.view(env.pid)                                       # default waterfall workflow, no releases
    for level in wf["levels"]:
        assert level_peers(wf, level[0]) == sorted(level)


async def test_a_release_stage_without_the_release_role_still_closes_the_release(rx):
    e = rx
    row = await e.pg.get_workflow(e.pid)
    sc = {s["key"]: s for s in row["config"]["stages"]}
    stages = [{**sc["build"], "dependsOn": ["runway"], "inputs": ["PRD"]},
              {**sc["review"], "dependsOn": ["build"]},
              {**sc["release"], "key": "ship", "agileRole": None, "dependsOn": ["review"], "inputs": ["REVIEW_REPORT"]}]
    rel = await e.agile.create_release(e.pid, e.po, name="Custom", stage_preset="custom", stages=stages)
    s = await e.agile.start_sprint(e.pid, e.po, release_id=rel["id"])
    await _approve(e, f"build@{s['label']}", by=e.admin)
    await _approve(e, f"review@{s['label']}")
    await e.agile.start_release_hardening(e.pid, e.po, rel["id"])
    await _approve(e, f"ship@{rel['code']}", by=e.admin)
    assert (await e.pg.get_release(rel["id"]))["status"] == "closed"


async def test_locked_answers_apply_whichever_way_a_release_is_created(rx):
    e = rx
    await e.agile.update_settings(e.pid, e.pm, {"release_defaults": {"stagePreset": "hotfix", "intakeRule": "epic", "usePool": False},
                                                "release_locks": ["stagePreset", "intakeRule", "usePool"]})
    made = await e.agile.create_release(e.pid, e.po, name="plain", stage_preset="inherit", intake_rule="pool", use_pool=True)
    assert (made["stagePreset"], made["intakeRule"], made["usePool"]) == ("hotfix", "epic", False)
    with pytest.raises(SdlcError) as err:
        await e.agile.set_release_stages(e.pid, e.po, made["id"], stage_preset="lean")
    assert err.value.code == "FORBIDDEN"


async def test_resume_repeats_the_stored_setup_and_only_for_a_release_left_part_way(rx):
    e = rx
    a = await _item(e, "left over")
    await e.backlog.claim_into_release(e.pid, e.po, e.r1["id"], [a["key"]])
    # a closed sprint in R-001 so it can be forked
    s = await e.agile.start_sprint(e.pid, e.po, release_id=e.r1["id"])
    for k in ("refine", "plan", "build", "review", "retro"):
        await _approve(e, f"{k}@{s['label']}", by=e.admin if k == "build" else None)
    answers = {"name": "v2", "startFrom": "fork", "sourceRelease": e.r1["id"], "unfinishedItems": "all"}
    real = e.backlog.move_unfinished

    async def boom(*_a, **_k):
        raise RuntimeError("hiccup")
    e.backlog.move_unfinished = boom
    with pytest.raises(SdlcError) as err:
        await e.setup.start(e.pid, e.po, answers)
    e.backlog.move_unfinished = real
    rid = err.value.details["releaseId"]
    # a resume with DIFFERENT answers still repeats what was stored ("move all")
    done = await e.setup.start(e.pid, e.po, {"name": "other", "unfinishedItems": "none"}, resume_release_id=rid)
    assert done["progress"]["moved"] == {"moved": 1}
    assert (await e.pg.get_backlog_item(e.pid, a["key"]))["release_id"] == rid
    # …and a finished release, or one the questionnaire never started, cannot be "resumed"
    for rel_id in (rid, e.r1["id"]):
        with pytest.raises(SdlcError) as err:
            await e.setup.start(e.pid, e.po, {"name": "x"}, resume_release_id=rel_id)
        assert err.value.code == "GATE_CONFLICT"
    assert (await e.pg.get_release(e.r1["id"]))["forked_from"] is None


async def test_a_custom_stage_release_can_be_resumed(rx):
    e = rx
    row = await e.pg.get_workflow(e.pid)
    sc = {s["key"]: s for s in row["config"]["stages"]}
    stages = [{**sc["build"], "dependsOn": ["runway"], "inputs": ["PRD"]}, {**sc["review"], "dependsOn": ["build"]}]
    real = e.agile.start_sprint
    calls = {"n": 0}

    async def once(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return await real(*a, **k)
    e.agile.start_sprint = once
    with pytest.raises(SdlcError) as err:
        await e.setup.start(e.pid, e.po, {"name": "Custom", "stagePreset": "custom", "stages": stages, "firstSprint": "now"})
    rel_id = err.value.details["releaseId"]
    out = await e.setup.start(e.pid, e.po, {}, resume_release_id=rel_id)         # the stored stages travel with the setup
    assert out["release"]["setupComplete"] is True and out["release"]["openIterationId"]


async def test_start_refuses_unknown_or_taken_epics_before_creating_anything(rx):
    e = rx
    n = len(await e.pg.list_releases(e.pid))
    with pytest.raises(SdlcError) as err:
        await e.setup.start(e.pid, e.po, {"name": "x", "intakeRule": "epic", "epics": ["DM-9999"]})
    assert err.value.code == "VALIDATION_FAILED" and "not an epic" in err.value.message
    assert len(await e.pg.list_releases(e.pid)) == n
    epic = await e.backlog.create(e.pid, e.po, {"title": "Pay", "type": "epic"})
    first = await e.setup.start(e.pid, e.po, {"name": "owner", "intakeRule": "epic", "epics": [epic["key"]]})
    with pytest.raises(SdlcError) as err:                                      # already mapped to a live release
        await e.setup.start(e.pid, e.po, {"name": "second", "intakeRule": "epic", "epics": [epic["key"]]})
    assert "already belongs to release" in err.value.message
    assert len(await e.pg.list_releases(e.pid)) == n + 1
    pre = await e.setup.preview(e.pid, e.po, {"name": "second", "intakeRule": "epic", "epics": [epic["key"]]})
    assert not pre["valid"]
    assert first["release"]["id"]


async def test_concurrent_epic_mapping_has_exactly_one_owner(rx):
    e = rx
    r2 = await e.agile.create_release(e.pid, e.po, name="B", intake_rule="epic")
    await e.pg.update_release(e.r1["id"], intake_rule="epic")
    for n in range(15):
        epic = await e.backlog.create(e.pid, e.po, {"title": f"E{n}", "type": "epic"})
        res = await asyncio.gather(*[e.pg.map_epic(e.pid, epic["id"], rid, None) for rid in (e.r1["id"], r2["id"]) * 2])
        owner = await e.pg.epic_mapping(epic["id"])
        want = [rid == owner["release_id"] for rid in (e.r1["id"], r2["id"]) * 2]
        assert res == want, (n, res, want)                                    # only the owner's calls succeed


async def test_an_epic_can_be_remapped_once_its_release_is_closed(rx):
    e = rx
    r2 = await e.agile.create_release(e.pid, e.po, name="B", intake_rule="epic")
    await e.pg.update_release(e.r1["id"], intake_rule="epic")
    epic = await e.backlog.create(e.pid, e.po, {"title": "E", "type": "epic"})
    await e.backlog.map_epic(e.pid, e.po, e.r1["id"], epic["key"])
    with pytest.raises(SdlcError):
        await e.backlog.map_epic(e.pid, e.po, r2["id"], epic["key"])
    await e.pg.set_release_status(e.r1["id"], "closed")
    await e.backlog.map_epic(e.pid, e.po, r2["id"], epic["key"])
    assert (await e.pg.epic_mapping(epic["id"]))["release_id"] == r2["id"]


async def test_items_committed_to_a_sprint_cannot_be_claimed_by_another_release(rx):
    e = rx
    r2 = await e.agile.create_release(e.pid, e.po, name="B")
    s = await e.agile.start_sprint(e.pid, e.po, release_id=e.r1["id"], capacity=20)
    a = await _item(e, "committed")
    await e.backlog.add_to_sprint(e.pid, e.po, a["key"], iteration_id=s["id"])
    await e.pg.pool.execute("UPDATE backlog_items SET release_id=NULL WHERE item_key=$1", a["key"])   # a legacy row
    res = await e.backlog.claim_into_release(e.pid, e.po, r2["id"], [a["key"]])
    assert res == {"claimed": [], "skipped": [a["key"]]}


async def test_presets_drop_ceremonies_by_role_not_by_stage_key():
    cfg = scrum_workflow()
    raw = cfg.model_dump()
    for s in raw["stages"]:
        if s["key"] == "plan":
            s["key"] = "sprint_planning"
        s["dependsOn"] = ["sprint_planning" if d == "plan" else d for d in s["dependsOn"]]
    from app.services.workflow_v2 import WorkflowConfig
    kept = [s["key"] for s in preset_stages(WorkflowConfig.model_validate(raw), "lean")]
    assert "sprint_planning" not in kept and {"refine", "build", "review", "retro"} <= set(kept)


async def test_the_wip_limit_is_per_release(rx):
    e = rx
    await e.agile.update_settings(e.pid, e.pm, {"wip_limit": 1})
    r2 = await e.agile.create_release(e.pid, e.po, name="B")
    s1 = await e.agile.start_sprint(e.pid, e.po, release_id=e.r1["id"], capacity=20)
    s2 = await e.agile.start_sprint(e.pid, e.po, release_id=r2["id"], capacity=20)
    a, a2, b = await _item(e, "a"), await _item(e, "a2"), await _item(e, "b")
    for it, s in ((a, s1), (a2, s1), (b, s2)):
        await e.backlog.add_to_sprint(e.pid, e.po, it["key"], iteration_id=s["id"])
    await e.backlog.set_status(e.pid, e.dev, a["key"], "in_progress", expected_version=None)
    with pytest.raises(SdlcError) as err:                                      # same release: limit reached
        await e.backlog.set_status(e.pid, e.dev, a2["key"], "in_progress", expected_version=None)
    assert "WIP limit" in err.value.message
    await e.backlog.set_status(e.pid, e.dev, b["key"], "in_progress", expected_version=None)   # other release: fine
