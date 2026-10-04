import asyncio

import asyncpg
import pytest

from app.repos.agile_pg import RANK_STEP

pytestmark = pytest.mark.asyncio


async def _release(pg, pid):
    return await pg.insert_release(project_id=pid, name="First release")


def _slots(base_seqs):
    def f(label, number, existing):
        top = max([i["seq"] for i in existing] + [8])
        first = number == 1
        return [((s if first else top + n), f"{k}@{label}", k) for n, (k, s) in enumerate(base_seqs, start=1)]
    return f


BASE = [("refine", 3), ("plan", 4), ("build", 5), ("review", 6), ("retro", 7)]


async def test_migration_relaxes_stage_slot_limit(pg, project):
    pid, uid = project
    await pg.pool.execute("UPDATE projects SET current_phase=250 WHERE id=$1", pid)   # was capped at 12
    await pg.pool.execute(
        "INSERT INTO artefacts (id, project_id, phase, type, title, content) VALUES ('a1',$1,999,'X','t','c')", pid)


async def test_release_numbers_and_codes_are_allocated_atomically(pg, project):
    pid, _ = project
    rels = await asyncio.gather(*[pg.insert_release(project_id=pid, name=f"r{i}") for i in range(8)])
    assert sorted(r["number"] for r in rels) == list(range(1, 9))
    assert {r["code"] for r in rels} == {f"R-{n:03d}" for n in range(1, 9)}


async def test_sprint_and_its_stage_slots_are_created_together(pg, project):
    pid, _ = project
    rel = await _release(pg, pid)
    it = await pg.create_iteration_with_instances(
        project_id=pid, release_id=rel["id"], goal="g", capacity=30, status="planned",
        starts_on=None, ends_on=None, slots_for=_slots(BASE))
    assert it["label"] == "S-001" and it["number"] == 1
    inst = await pg.list_stage_instances(pid)
    assert [(i["seq"], i["key"]) for i in inst] == [(3, "refine@S-001"), (4, "plan@S-001"), (5, "build@S-001"),
                                                    (6, "review@S-001"), (7, "retro@S-001")]


async def test_only_one_open_sprint_even_under_a_race(pg, project):
    pid, _ = project
    rel = await _release(pg, pid)

    async def start():
        return await pg.create_iteration_with_instances(
            project_id=pid, release_id=rel["id"], goal="g", capacity=1, status="planned",
            starts_on=None, ends_on=None, slots_for=_slots(BASE))

    results = await asyncio.gather(*[start() for _ in range(6)], return_exceptions=True)
    ok = [r for r in results if not isinstance(r, Exception)]
    assert len(ok) == 1
    assert all(isinstance(r, asyncpg.UniqueViolationError) for r in results if isinstance(r, Exception))
    assert len(await pg.list_iterations(pid)) == 1 and len(await pg.list_stage_instances(pid)) == 5
    # rolled-back attempts left no half-created slots behind


async def test_second_sprint_after_close_gets_fresh_slots_and_next_number(pg, project):
    pid, _ = project
    rel = await _release(pg, pid)
    it1 = await pg.create_iteration_with_instances(
        project_id=pid, release_id=rel["id"], goal="", capacity=1, status="active",
        starts_on=None, ends_on=None, slots_for=_slots(BASE))
    await pg.update_iteration(it1["id"], status="closed")
    it2 = await pg.create_iteration_with_instances(
        project_id=pid, release_id=rel["id"], goal="", capacity=1, status="planned",
        starts_on=None, ends_on=None, slots_for=_slots(BASE))
    assert it2["label"] == "S-002"
    seqs = [i["seq"] for i in await pg.list_stage_instances(pid)]
    assert len(seqs) == len(set(seqs)) == 10 and max(seqs) > 8


async def test_release_hardening_materialises_slots_once(pg, project):
    pid, _ = project
    rel = await _release(pg, pid)

    def slots(code, n, existing):
        return [(8, f"release@{code}", "release")]

    rows = await pg.create_release_instances(project_id=pid, release_id=rel["id"], slots_for=slots)
    assert [r["key"] for r in rows] == ["release@R-001"]
    assert (await pg.get_release(rel["id"]))["status"] == "hardening"
    with pytest.raises(ValueError):                       # cannot be started twice
        await pg.create_release_instances(project_id=pid, release_id=rel["id"], slots_for=slots)


async def test_backlog_keys_are_unique_and_sequential_under_concurrency(pg, project):
    pid, uid = project
    rows = await asyncio.gather(*[
        pg.insert_backlog_item(project_id=pid, created_by=uid, title=f"s{i}") for i in range(25)])
    keys = sorted(int(r["item_key"].split("-")[1]) for r in rows)
    assert keys == list(range(1, 26))
    ranks = sorted(r["rank"] for r in rows)
    assert len(set(ranks)) == 25 and ranks[1] - ranks[0] >= RANK_STEP - 1e-6


async def test_optimistic_concurrency_rejects_a_stale_write(pg, project):
    pid, uid = project
    it = await pg.insert_backlog_item(project_id=pid, created_by=uid, title="a")
    assert it["version"] == 1
    ok = await pg.update_backlog_item(pid, it["id"], expected_version=1, title="b")
    assert ok["title"] == "b" and ok["version"] == 2
    stale = await pg.update_backlog_item(pid, it["id"], expected_version=1, title="c")
    assert stale is None
    assert (await pg.get_backlog_item(pid, "DM-1"))["title"] == "b"           # lookup by key works too
    with pytest.raises(ValueError):
        await pg.update_backlog_item(pid, it["id"], expected_version=2, version=99)


async def test_sprint_close_returns_unfinished_items_to_the_backlog(pg, project):
    pid, uid = project
    rel = await _release(pg, pid)
    it = await pg.create_iteration_with_instances(
        project_id=pid, release_id=rel["id"], goal="", capacity=10, status="active",
        starts_on=None, ends_on=None, slots_for=_slots(BASE))
    a = await pg.insert_backlog_item(project_id=pid, created_by=uid, title="a", estimate=3)
    b = await pg.insert_backlog_item(project_id=pid, created_by=uid, title="b", estimate=5)
    await pg.assign_items_to_iteration(pid, [a["id"], b["id"]], it["id"], "in_sprint")
    await pg.update_backlog_item(pid, a["id"], expected_version=None, status="done")
    moved = await pg.release_unfinished_items(it["id"])
    assert moved == [b["id"]]
    b2 = await pg.get_backlog_item(pid, b["id"])
    assert b2["status"] == "ready" and b2["iteration_id"] is None
    assert (await pg.get_backlog_item(pid, a["id"]))["status"] == "done"       # done work stays attached


async def test_jira_key_is_unique_per_project(pg, project):
    pid, uid = project
    await pg.insert_backlog_item(project_id=pid, created_by=uid, title="a", jira_key="SHOP-1")
    with pytest.raises(asyncpg.UniqueViolationError):
        await pg.insert_backlog_item(project_id=pid, created_by=uid, title="b", jira_key="SHOP-1")
    assert (await pg.get_backlog_by_jira(pid, "SHOP-1"))["title"] == "a"


async def test_invalid_values_are_rejected_by_the_database(pg, project):
    pid, uid = project
    with pytest.raises(asyncpg.CheckViolationError):
        await pg.insert_backlog_item(project_id=pid, created_by=uid, title="x", status="bogus")
    with pytest.raises(asyncpg.CheckViolationError):
        await pg.insert_backlog_item(project_id=pid, created_by=uid, title="x", estimate=-1)
    with pytest.raises(asyncpg.CheckViolationError):
        await pg.insert_backlog_item(project_id=pid, created_by=uid, title="")


async def test_new_proposal_supersedes_the_open_one_and_decisions_are_once_only(pg, project):
    pid, uid = project
    p1 = await pg.insert_proposal(project_id=pid, iteration_id=None, phase=3, kind="refine",
                                  payload={"items": []}, warnings=[])
    p2 = await pg.insert_proposal(project_id=pid, iteration_id=None, phase=3, kind="refine",
                                  payload={"items": [1]}, warnings=["w"])
    assert (await pg.get_proposal(p1["id"]))["status"] == "superseded"
    assert (await pg.latest_proposal(pid, 3, "refine"))["id"] == p2["id"]
    assert await pg.set_proposal_status(p2["id"], "applied", uid) is True
    assert await pg.set_proposal_status(p2["id"], "applied", uid) is False       # idempotent apply
    assert await pg.update_proposal(p2["id"], expected_version=1, payload={}, warnings=[]) is None  # decided


async def test_project_delete_cascades_every_agile_table(pg, project):
    pid, uid = project
    rel = await _release(pg, pid)
    await pg.create_iteration_with_instances(project_id=pid, release_id=rel["id"], goal="", capacity=1,
                                             status="planned", starts_on=None, ends_on=None, slots_for=_slots(BASE))
    await pg.insert_backlog_item(project_id=pid, created_by=uid, title="a")
    await pg.insert_proposal(project_id=pid, iteration_id=None, phase=3, kind="plan", payload={}, warnings=[])
    await pg.save_sync_state(pid, watermark=None, status="ok", error=None, stats={})
    await pg.pool.execute("DELETE FROM projects WHERE id=$1", pid)
    for t in ("releases", "iterations", "stage_instances", "backlog_items", "backlog_counters",
              "agile_proposals", "agile_sync_state"):
        assert await pg.pool.fetchval(f"SELECT COUNT(*) FROM {t} WHERE project_id=$1", pid) == 0, t
