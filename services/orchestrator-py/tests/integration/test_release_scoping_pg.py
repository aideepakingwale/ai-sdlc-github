"""Backlog scoping across parallel releases: the shared pool, claiming, moving, epic mapping, Jira intake routing."""

import asyncio

import pytest

from app.agile.jira_sync import JiraSyncService
from app.domain.errors import SdlcError

from .helpers import _finish_project_stages
from .test_jira_sync_pg import FakeJira

pytestmark = pytest.mark.asyncio


async def _two_releases(e, **second):
    await e.agile.enable(e.pid, e.pm, methodology="scrum")
    await _finish_project_stages(e)
    r1 = (await e.pg.list_releases(e.pid))[0]
    r2 = await e.agile.create_release(e.pid, e.po, name="Second", **second)
    return r1, r2


async def _item(e, title, *, est=3, ready=True, release=None, typ="story"):
    data = {"title": title, "type": typ}
    if typ != "epic":
        data |= {"estimate": est, "acceptanceCriteria": ["ok"]}
    if release:
        data["releaseId"] = release
    v = await e.backlog.create(e.pid, e.po, data)
    if ready and typ != "epic":
        v = await e.backlog.set_status(e.pid, e.po, v["key"], "ready", expected_version=v["version"])
    return v


async def test_items_belong_to_a_release_or_the_shared_pool(env):
    e = env
    r1, r2 = await _two_releases(e)
    a, b, c = await _item(e, "pool"), await _item(e, "in r1", release=r1["id"]), await _item(e, "in r2", release=r2["id"])
    assert (a["releaseId"], b["releaseId"], c["releaseId"]) == (None, r1["id"], r2["id"])
    keys = lambda res: sorted(i["key"] for i in res["items"])                          # noqa: E731
    assert keys(await e.backlog.list(e.pid, e.po)) == sorted([a["key"], b["key"], c["key"]])
    assert keys(await e.backlog.list(e.pid, e.po, scope="pool")) == [a["key"]]
    assert keys(await e.backlog.list(e.pid, e.po, scope="release", release_id=r1["id"])) == [b["key"]]
    assert keys(await e.backlog.list(e.pid, e.po, scope="eligible", release_id=r2["id"])) == sorted([a["key"], c["key"]])
    with pytest.raises(SdlcError):
        await e.backlog.list(e.pid, e.po, scope="release")                              # needs a releaseId
    with pytest.raises(SdlcError):
        await e.backlog.create(e.pid, e.po, {"title": "x", "releaseId": "nope"})


async def test_committing_a_pool_item_to_a_sprint_claims_it_for_that_release(env):
    e = env
    r1, r2 = await _two_releases(e)
    s1 = await e.agile.start_sprint(e.pid, e.po, release_id=r1["id"], capacity=20)
    s2 = await e.agile.start_sprint(e.pid, e.po, release_id=r2["id"], capacity=20)
    pool = await _item(e, "pool story")
    with pytest.raises(SdlcError) as err:                                               # two open sprints: must say which
        await e.backlog.add_to_sprint(e.pid, e.po, pool["key"])
    assert err.value.code == "VALIDATION_FAILED"
    added = await e.backlog.add_to_sprint(e.pid, e.po, pool["key"], iteration_id=s1["id"])
    assert added["releaseId"] == r1["id"] and added["iterationId"] == s1["id"]
    other = await _item(e, "r2 story", release=r2["id"])
    with pytest.raises(SdlcError) as err:                                               # another release's item
        await e.backlog.add_to_sprint(e.pid, e.po, other["key"], iteration_id=s1["id"])
    assert "another release" in err.value.message
    ok = await e.backlog.add_to_sprint(e.pid, e.po, other["key"])                       # item's own release picks the sprint
    assert ok["iterationId"] == s2["id"]


async def test_a_release_that_does_not_use_the_pool_refuses_pool_items(env):
    e = env
    r1, r2 = await _two_releases(e, use_pool=False)
    s2 = await e.agile.start_sprint(e.pid, e.po, release_id=r2["id"])
    pool = await _item(e, "pool story")
    with pytest.raises(SdlcError) as err:
        await e.backlog.add_to_sprint(e.pid, e.po, pool["key"], iteration_id=s2["id"])
    assert "does not draw from" in err.value.message
    await e.backlog.claim_into_release(e.pid, e.po, r2["id"], [pool["key"]])             # explicit claim still works
    assert (await e.backlog.add_to_sprint(e.pid, e.po, pool["key"]))["releaseId"] == r2["id"]


async def test_planning_and_refinement_only_see_their_release(env):
    from app.agile.proposals import LlmPlan, LlmPlanPick
    e = env
    r1, r2 = await _two_releases(e, use_pool=False)
    s2 = await e.agile.start_sprint(e.pid, e.po, release_id=r2["id"], capacity=50)
    mine, theirs, pool = (await _item(e, "mine", release=r2["id"]), await _item(e, "theirs", release=r1["id"]),
                          await _item(e, "pool"))
    visible = {r["item_key"] for r in await e.pg.list_backlog_for_iteration(e.pid, s2["id"])}
    assert visible == {mine["key"]}                                                     # pool closed, other release hidden
    plan = await e.proposals.create_plan(e.pid, 99, await e.pg.get_iteration(s2["id"]),
                                         LlmPlan(picks=[LlmPlanPick(key=k, reason="") for k in (mine["key"], theirs["key"], pool["key"])]))
    assert [i["key"] for i in plan["payload"]["items"]] == [mine["key"]]
    # even if a stale proposal names them, the repository refuses to commit another release's items
    res = await e.pg.apply_plan(project_id=e.pid, proposal_id=plan["id"], iteration_id=s2["id"],
                                keys=[mine["key"], theirs["key"], pool["key"]], goal="", actor_id=None)
    assert res["assigned"] == [mine["key"]] and sorted(res["skipped"]) == sorted([theirs["key"], pool["key"]])


async def test_moving_unfinished_items_keeps_jira_keys_and_leaves_started_work(env):
    e = env
    r1, r2 = await _two_releases(e)
    s1 = await e.agile.start_sprint(e.pid, e.po, release_id=r1["id"], capacity=50)
    a, b, c = (await _item(e, "ready", release=r1["id"]), await _item(e, "in sprint", release=r1["id"]),
               await _item(e, "new one", release=r1["id"], ready=False))
    await e.backlog.add_to_sprint(e.pid, e.po, b["key"])
    await e.pg.pool.execute("UPDATE backlog_items SET jira_key='SHOP-7' WHERE item_key=$1", a["key"])
    res = await e.backlog.move_unfinished(e.pid, e.po, r1["id"], r2["id"])
    assert sorted(res["moved"]) == sorted([a["key"], c["key"]])
    assert [s["reason"] for s in res["skipped"]] == ["is in_sprint in a sprint"]
    moved = await e.pg.get_backlog_item(e.pid, a["key"])
    assert moved["release_id"] == r2["id"] and moved["jira_key"] == "SHOP-7"
    assert (await e.pg.get_backlog_item(e.pid, b["key"]))["release_id"] == r1["id"]
    with pytest.raises(SdlcError):
        await e.backlog.move_unfinished(e.pid, e.po, r1["id"], r1["id"])                # same release
    assert s1["id"]


async def test_concurrent_claims_of_one_pool_item_have_one_winner(env):
    e = env
    r1, r2 = await _two_releases(e)
    pool = await _item(e, "contested")
    res = await asyncio.gather(e.backlog.claim_into_release(e.pid, e.po, r1["id"], [pool["key"]]),
                               e.backlog.claim_into_release(e.pid, e.po, r2["id"], [pool["key"]]))
    assert sorted(len(r["claimed"]) for r in res) == [0, 1]
    row = await e.pg.get_backlog_item(e.pid, pool["key"])
    assert row["release_id"] in (r1["id"], r2["id"])


async def test_epic_mapping_rules(env):
    e = env
    r1, r2 = await _two_releases(e, intake_rule="epic")
    epic = await _item(e, "Payments", typ="epic")
    child = await e.backlog.create(e.pid, e.po, {"title": "Pay", "epicKey": epic["key"], "estimate": 2, "acceptanceCriteria": ["a"]})
    with pytest.raises(SdlcError) as err:                                               # r1 takes work from the pool
        await e.backlog.map_epic(e.pid, e.po, r1["id"], epic["key"])
    assert "intake rule" in err.value.message
    prev = await e.backlog.map_epic(e.pid, e.po, r2["id"], epic["key"], preview=True)
    assert prev["poolItems"] == [child["key"]]
    assert (await e.pg.get_backlog_item(e.pid, child["key"]))["release_id"] is None     # a preview changes nothing
    done = await e.backlog.map_epic(e.pid, e.po, r2["id"], epic["key"], adopt_existing=True)
    assert done["adopted"] == [child["key"]]
    assert (await e.pg.get_backlog_item(e.pid, child["key"]))["release_id"] == r2["id"]
    assert (await e.pg.get_backlog_item(e.pid, epic["key"]))["release_id"] == r2["id"]
    await e.backlog.map_epic(e.pid, e.po, r2["id"], epic["key"])                        # idempotent
    await e.pg.update_release(r1["id"], intake_rule="epic")
    with pytest.raises(SdlcError) as err:                                               # one epic, one release
        await e.backlog.map_epic(e.pid, e.po, r1["id"], epic["key"])
    assert "another release" in err.value.message
    assert [x["epicKey"] for x in await e.backlog.epics_of_release(e.pid, e.po, r2["id"])] == [epic["key"]]
    await e.backlog.unmap_epic(e.pid, e.po, epic["key"])
    assert await e.backlog.epics_of_release(e.pid, e.po, r2["id"]) == []
    with pytest.raises(SdlcError):
        await e.backlog.map_epic(e.pid, e.po, r2["id"], child["key"])                   # not an epic


@pytest.fixture
async def jx(env):
    e = env
    await e.pg.pool.execute("UPDATE projects SET jira_project_key='SHOP' WHERE id=$1", e.pid)
    e.jira = FakeJira()
    e.sync = JiraSyncService(e.pg, e.jira, e.audit, e.agile)
    e.backlog.sync_hook = e.sync.write_through
    return e


async def test_jira_intake_routes_new_issues_by_epic_and_otherwise_to_the_pool(jx):
    e = jx
    r1, r2 = await _two_releases(e, intake_rule="epic")
    e.jira.add("SHOP-1", "Epic", "Payments")
    e.jira.add("SHOP-2", "Epic", "Search")
    await e.sync.sync(e.pid, e.po)
    epic = await e.pg.get_backlog_by_jira(e.pid, "SHOP-1")
    await e.backlog.map_epic(e.pid, e.po, r2["id"], epic["item_key"])
    e.jira.add("SHOP-3", "Story", "Pay by card", ac=["a"], epic="SHOP-1")
    e.jira.add("SHOP-4", "Story", "Search box", ac=["a"], epic="SHOP-2")                 # epic not mapped
    e.jira.add("SHOP-5", "Story", "Orphan", ac=["a"])                                    # no epic
    await e.sync.sync(e.pid, e.po)
    rel = {k: (await e.pg.get_backlog_by_jira(e.pid, k))["release_id"] for k in ("SHOP-3", "SHOP-4", "SHOP-5")}
    assert rel == {"SHOP-3": r2["id"], "SHOP-4": None, "SHOP-5": None}
    # routing happens once: re-linking the issue to another epic in Jira does not move it
    e.jira.touch("SHOP-3", epicKey="SHOP-2")
    await e.sync.sync(e.pid, e.po)
    assert (await e.pg.get_backlog_by_jira(e.pid, "SHOP-3"))["release_id"] == r2["id"]


async def test_jira_intake_ignores_a_mapping_of_a_release_that_takes_from_the_pool(jx):
    e = jx
    r1, r2 = await _two_releases(e, intake_rule="epic")
    e.jira.add("SHOP-1", "Epic", "Payments")
    await e.sync.sync(e.pid, e.po)
    epic = await e.pg.get_backlog_by_jira(e.pid, "SHOP-1")
    await e.backlog.map_epic(e.pid, e.po, r2["id"], epic["item_key"])
    await e.pg.update_release(r2["id"], intake_rule="pool")                              # the rule changed afterwards
    e.jira.add("SHOP-2", "Story", "Late", ac=["a"], epic="SHOP-1")
    await e.sync.sync(e.pid, e.po)
    assert (await e.pg.get_backlog_by_jira(e.pid, "SHOP-2"))["release_id"] is None
