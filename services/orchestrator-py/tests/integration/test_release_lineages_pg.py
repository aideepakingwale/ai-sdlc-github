"""Parallel release lineages: independent sprint chains, per-release stage sets, forking rules."""

import pytest

from app.domain.errors import SdlcError

from .helpers import _approve, _finish_project_stages, _stage

pytestmark = pytest.mark.asyncio


async def _setup(e):
    await e.agile.enable(e.pid, e.pm, methodology="scrum")
    await _finish_project_stages(e)
    rels = await e.pg.list_releases(e.pid)
    return rels[0]


async def _run_sprint(e, label, keys=("refine", "plan", "build", "review", "retro")):
    for k in keys:
        await _approve(e, f"{k}@{label}", by=e.admin if k == "build" else None)


async def test_two_releases_run_sprints_in_parallel_with_independent_chains(env):
    e = env
    r1 = await _setup(e)
    r2 = await e.agile.create_release(e.pid, e.po, name="Mobile app")
    assert r2["code"] == "R-002" and r2["forkedFrom"] is None
    with pytest.raises(SdlcError) as err:                                  # two open releases: must say which
        await e.agile.start_sprint(e.pid, e.po)
    assert err.value.code == "VALIDATION_FAILED"
    s1 = await e.agile.start_sprint(e.pid, e.po, release_id=r1["id"])
    s2 = await e.agile.start_sprint(e.pid, e.po, release_id=r2["id"])
    assert (s1["label"], s2["label"]) == ("S-001", "S-002")               # one global numbering
    with pytest.raises(SdlcError) as err:                                  # one open sprint PER release
        await e.agile.start_sprint(e.pid, e.po, release_id=r1["id"])
    assert "already has an open sprint" in err.value.message
    wf = await e.wf.view(e.pid)
    e2 = next(s for s in wf["stages"] if s["key"] == "refine@S-002")
    assert "retro@S-001" not in e2["dependsOn"]                            # R-002's chain does not wait for R-001
    # advancing one release never waits on, nor unlocks, the other's stages
    await _approve(e, "refine@S-001")
    assert (await e.dynamo.get_phase_state(e.pid, e2["seq"]) or {}).get("status") in (None, "NOT_STARTED")
    await _run_sprint(e, "S-002")
    its = {i["label"]: i["status"] for i in await e.pg.list_iterations(e.pid)}
    assert its["S-002"] == "closed" and its["S-001"] == "planned"
    await _run_sprint(e, "S-001", keys=("plan", "build", "review", "retro"))
    assert {i["status"] for i in await e.pg.list_iterations(e.pid)} == {"closed"}
    ov = await e.agile.overview(e.pid, e.pm)
    assert len(ov["releases"]) == 2 and ov["openIterations"] == []


async def test_gate_advancement_is_scoped_to_the_release(env):
    """Two sprints of different releases sit at the same level; approving a stage must not wait for the other."""
    e = env
    r1 = await _setup(e)
    r2 = await e.agile.create_release(e.pid, e.po, name="Other")
    await e.agile.start_sprint(e.pid, e.po, release_id=r1["id"])
    await e.agile.start_sprint(e.pid, e.po, release_id=r2["id"])
    res = await _approve(e, "refine@S-001")
    plan1 = await _stage(e, "plan@S-001")
    assert res["nextPhase"] == plan1["seq"]                               # advanced although R-002's refine is pending


async def test_forking_needs_a_closed_sprint_and_records_the_baseline(env):
    e = env
    r1 = await _setup(e)
    with pytest.raises(SdlcError) as err:
        await e.agile.create_release(e.pid, e.po, name="Hotfix", forked_from=r1["id"])
    assert err.value.code == "GATE_CONFLICT" and "no closed sprint" in err.value.message
    await e.agile.start_sprint(e.pid, e.po)
    await _run_sprint(e, "S-001")
    fork = await e.agile.create_release(e.pid, e.po, name="Hotfix", forked_from=r1["id"], intake_rule="epic", use_pool=False)
    assert fork["forkedFrom"] == "R-001" and fork["forkBaseline"]["sprint"] == "S-001"
    assert (fork["intakeRule"], fork["usePool"]) == ("epic", False)
    with pytest.raises(SdlcError):
        await e.agile.create_release(e.pid, e.po, name="x", forked_from="nope")
    for bad in ({"name": ""}, {"name": "x", "intake_rule": "fix-version"}, {"name": "x", "stage_preset": "bogus"}):
        with pytest.raises(SdlcError) as err:
            await e.agile.create_release(e.pid, e.po, **bad)
        assert err.value.code == "VALIDATION_FAILED"
    with pytest.raises(SdlcError) as err:
        await e.agile.create_release(e.pid, e.dev, name="by a developer")
    assert err.value.code == "FORBIDDEN"


async def test_a_release_can_run_a_different_stage_set(env):
    e = env
    await _setup(e)
    hot = await e.agile.create_release(e.pid, e.po, name="Hotfix", stage_preset="hotfix")
    s = await e.agile.start_sprint(e.pid, e.po, release_id=hot["id"])
    assert s["status"] == "active"                                         # no planning ceremony → live at once
    wf = await e.wf.view(e.pid)
    mine = [x for x in wf["stages"] if x.get("iterationLabel") == s["label"]]
    assert [x["baseKey"] for x in mine] == ["build", "review"]
    assert next(x for x in mine if x["baseKey"] == "build")["dependsOn"] == ["runway"]
    await _approve(e, f"build@{s['label']}", by=e.admin)
    await _approve(e, f"review@{s['label']}")                              # the stage set's closing stage closes the sprint
    assert (await e.pg.get_iteration(s["id"]))["status"] == "closed"
    rel = await e.agile.start_release_hardening(e.pid, e.po, hot["id"])
    assert rel["status"] == "hardening"
    wf = await e.wf.view(e.pid)
    assert any(x["baseKey"] == "release" and x.get("releaseId") == hot["id"] for x in wf["stages"])
    # the project's own release is untouched
    other = await e.pg.get_release((await e.pg.list_releases(e.pid))[0]["id"])
    assert other["workflow"] is None


async def test_build_only_release_has_no_release_stage_and_closes_directly(env):
    e = env
    await _setup(e)
    lean = await e.agile.create_release(e.pid, e.po, name="Spike", stage_preset="build-only")
    s = await e.agile.start_sprint(e.pid, e.po, release_id=lean["id"])
    await _approve(e, f"build@{s['label']}", by=e.admin)
    await _approve(e, f"review@{s['label']}")
    assert (await e.pg.get_iteration(s["id"]))["status"] == "closed"
    done = await e.agile.start_release_hardening(e.pid, e.po, lean["id"])
    assert done["status"] == "closed"


async def test_custom_stage_sets_are_validated_and_frozen_once_started(env):
    e = env
    await _setup(e)
    row = await e.pg.get_workflow(e.pid)
    stages = [s for s in row["config"]["stages"] if s["scope"] == "iteration" and s["key"] in ("build", "review")]
    stages = [{**s, "dependsOn": ["runway"] if s["key"] == "build" else ["build"],
               "inputs": ["PRD"] if s["key"] == "build" else ["INCREMENT_NOTES"]} for s in stages]
    unwired = [{**stages[0], "inputs": ["SPRINT_PLAN"]}, stages[1]]               # input nobody produces
    with pytest.raises(SdlcError) as err:
        await e.agile.create_release(e.pid, e.po, name="bad", stage_preset="custom", stages=unwired)
    assert err.value.code == "VALIDATION_FAILED" and "not produced" in err.value.message
    foundation = {**row["config"]["stages"][0]}                             # a project-scoped stage is not allowed here
    with pytest.raises(SdlcError) as err:
        await e.agile.create_release(e.pid, e.po, name="bad", stage_preset="custom", stages=[foundation, *stages])
    assert err.value.code == "VALIDATION_FAILED"
    broken = [{**stages[0], "dependsOn": ["ghost"]}, stages[1]]
    with pytest.raises(SdlcError) as err:
        await e.agile.create_release(e.pid, e.po, name="bad", stage_preset="custom", stages=broken)
    assert err.value.code == "VALIDATION_FAILED"
    ok = await e.agile.create_release(e.pid, e.po, name="Custom", stage_preset="custom", stages=stages)
    again = await e.agile.set_release_stages(e.pid, e.po, ok["id"], stage_preset="lean")
    assert again["id"] == ok["id"]
    await e.agile.start_sprint(e.pid, e.po, release_id=ok["id"])
    with pytest.raises(SdlcError) as err:
        await e.agile.set_release_stages(e.pid, e.po, ok["id"], stage_preset="hotfix")
    assert "frozen" in err.value.message


async def test_closing_a_release_opens_the_next_only_when_no_other_is_live(env):
    e = env
    r1 = await _setup(e)
    r2 = await e.agile.create_release(e.pid, e.po, name="Parallel")
    s1 = await e.agile.start_sprint(e.pid, e.po, release_id=r1["id"])
    await _run_sprint(e, s1["label"])
    await e.agile.start_release_hardening(e.pid, e.po, r1["id"])
    await _approve(e, "release@R-001", by=e.admin)
    rels = {r["code"]: r["status"] for r in await e.pg.list_releases(e.pid)}
    assert rels == {"R-001": "closed", "R-002": "open"}                    # R-002 was live: nothing new opened
    s2 = await e.agile.start_sprint(e.pid, e.po, release_id=r2["id"])
    await _run_sprint(e, s2["label"])
    await e.agile.start_release_hardening(e.pid, e.po, r2["id"])
    await _approve(e, "release@R-002", by=e.admin)
    rels = {r["code"]: r["status"] for r in await e.pg.list_releases(e.pid)}
    assert rels == {"R-001": "closed", "R-002": "closed", "R-003": "open"}  # none live → the next one opens
