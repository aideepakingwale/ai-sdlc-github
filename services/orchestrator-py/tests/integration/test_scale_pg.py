"""Hot paths stay fast and bounded with 60 sprints (~300 stage slots) on a real Postgres."""

import time

import pytest

from app.agile.engine import InstanceRef, allocate_seqs, instance_key
from app.services.flow import FlowService


pytestmark = pytest.mark.asyncio

SPRINTS = 60


async def seed(e, n=SPRINTS):
    await e.agile.enable(e.pid, e.pm, methodology="scrum")
    base = await e.agile._base_view(e.pid)
    rel = (await e.pg.list_releases(e.pid))[0]
    for i in range(n):
        def slots_for(label, number, existing, _b=base):
            refs = [InstanceRef(x["seq"], x["key"], x["base_key"], x["scope"], x["iteration_id"], x["release_id"]) for x in existing]
            return [(s, instance_key(k, label), k) for k, s in allocate_seqs(_b["stages"], refs, scope="iteration", first=number == 1).items()]
        it = await e.pg.create_iteration_with_instances(project_id=e.pid, release_id=rel["id"], goal="g", capacity=20, status="active",
                                                        starts_on=None, ends_on=None, slots_for=slots_for)
        await e.pg.update_iteration(it["id"], status="closed", summary={"velocity": 18, "completed": 6})


async def timed(fn, *a, runs=5):
    best = 1e9
    for _ in range(runs):
        t = time.perf_counter()
        await fn(*a)
        best = min(best, time.perf_counter() - t)
    return best * 1000


async def test_hot_paths_at_sixty_sprints(env):
    e = env
    await seed(e)
    view = await e.wf.view(e.pid)
    assert len(view["stages"]) == 2 + 5 * SPRINTS and len({s["seq"] for s in view["stages"]}) == len(view["stages"])
    # sprint N+1 waits on sprint N all the way down the chain; levels = foundation(2) + 5 per sprint
    assert len(view["levels"]) == 2 + 5 * SPRINTS

    flow = FlowService(e.pg, e.dynamo, e.audit, e.authz, e.content if hasattr(e, "content") else None, e.wf, None)
    ms = {
        "workflow.view": await timed(e.wf.view, e.pid),
        "flow": await timed(flow.flow, e.pid, e.admin),
        "gates.list_states": await timed(e.gates.list_states, e.pid, e.admin),
        "agile.overview": await timed(e.agile.overview, e.pid, e.pm),
    }
    print("\nlatency at 60 sprints (best of 5, ms):", {k: round(v, 1) for k, v in ms.items()})
    for name, v in ms.items():
        assert v < 750, f"{name} took {v:.0f} ms at {SPRINTS} sprints"


async def test_view_cost_grows_linearly_not_quadratically(env):
    e = env
    await seed(e, 15)
    small = await timed(e.wf.view, e.pid)
    await seed_more(e, 45)
    large = await timed(e.wf.view, e.pid)
    assert large < small * 12 + 50, f"15 sprints {small:.1f} ms -> 60 sprints {large:.1f} ms"      # 4x the work, not 16x


async def seed_more(e, n):
    base = await e.agile._base_view(e.pid)
    rel = (await e.pg.list_releases(e.pid))[0]
    for _ in range(n):
        await e.pg.update_iteration((await e.pg.list_iterations(e.pid))[-1]["id"], status="closed")
        def slots_for(label, number, existing, _b=base):
            refs = [InstanceRef(x["seq"], x["key"], x["base_key"], x["scope"], x["iteration_id"], x["release_id"]) for x in existing]
            return [(s, instance_key(k, label), k) for k, s in allocate_seqs(_b["stages"], refs, scope="iteration", first=number == 1).items()]
        await e.pg.create_iteration_with_instances(project_id=e.pid, release_id=rel["id"], goal="g", capacity=20, status="active",
                                                   starts_on=None, ends_on=None, slots_for=slots_for)


async def test_flow_windows_old_sprints_and_keeps_everything_reachable(env):
    import json

    e = env
    await seed(e)
    flow = FlowService(e.pg, e.dynamo, e.audit, e.authz, None, e.wf, None)
    # mark every seeded stage approved (the seeded sprints are closed)
    for s in (await e.wf.view(e.pid))["stages"]:
        await e.dynamo.put_phase_state(project_id=e.pid, phase=s["seq"], status="APPROVED", reviewer_role="PO")
    full = await flow.flow(e.pid, e.admin, window=False)
    win = await flow.flow(e.pid, e.admin)
    assert len(full["stages"]) == 302 and full["collapsedSprints"] == []
    labels = {s["iterationLabel"] for s in win["stages"] if s["iterationLabel"]}
    assert labels == {"S-059", "S-060"}                                    # the two most recent closed sprints
    assert [s["key"] for s in win["stages"][:2]] == ["vision", "runway"]   # foundation always present
    assert len(win["collapsedSprints"]) == 58 and win["collapsedSprints"][0] == {
        "label": "S-001", "number": 1, "status": "closed", "stages": 5, "approved": 5}
    assert len(json.dumps(win, default=str)) < len(json.dumps(full, default=str)) / 10        # payload shrinks > 10x
    assert all(q in {s["phase"] for s in win["stages"]} for lv in win["levels"] for q in lv)    # levels stay consistent
    asked = await flow.flow(e.pid, e.admin, ["S-003"])
    assert "S-003" in {s["iterationLabel"] for s in asked["stages"]} and len(asked["collapsedSprints"]) == 57


async def test_an_open_sprint_is_never_collapsed(env):
    e = env
    await seed(e, 8)
    it = (await e.pg.list_iterations(e.pid))[-1]
    await e.pg.update_iteration(it["id"], status="active")                     # reopen the last sprint
    flow = FlowService(e.pg, e.dynamo, e.audit, e.authz, None, e.wf, None)
    for s in (await e.wf.view(e.pid))["stages"]:
        if s["iterationLabel"] != "S-008":
            await e.dynamo.put_phase_state(project_id=e.pid, phase=s["seq"], status="APPROVED", reviewer_role="PO")
    win = await flow.flow(e.pid, e.admin)
    assert "S-008" in {s["iterationLabel"] for s in win["stages"]}                # in progress → full detail
    assert {c["label"] for c in win["collapsedSprints"]} == {f"S-00{n}" for n in range(1, 6)}
