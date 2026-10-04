"""Jira ⇄ backlog sync on a real Postgres against an in-memory Jira that mirrors the connector's tool semantics."""

import datetime as dt

import pytest

from app.agile.jira_sync import OVERLAP, JiraSyncService
from app.domain.errors import SdlcError

pytestmark = pytest.mark.asyncio

NOW = dt.datetime(2026, 5, 1, 9, 0, tzinfo=dt.UTC)


class FakeJira:
    """Same tool contract as services/tool-connector-service (search/get/update/transition/create)."""

    def __init__(self):
        self.issues: dict[str, dict] = {}
        self.clock = NOW
        self.calls: list[tuple[str, dict]] = []
        self.n = 0
        self.fail_search = False

    def tick(self, minutes=1):
        self.clock += dt.timedelta(minutes=minutes)
        return self.clock.isoformat()

    def add(self, key, type_="Story", summary="S", cat="todo", points=None, ac=(), epic=None, labels=()):
        self.issues[key] = {"key": key, "id": key, "url": f"http://j/{key}", "summary": summary, "description": "",
                            "type": type_, "status": {"todo": "To Do", "inprogress": "In Progress", "done": "Done"}[cat],
                            "statusCategory": cat, "priority": None, "storyPoints": points, "labels": list(labels),
                            "epicKey": epic, "sprint": None, "assignee": None, "created": self.tick(),
                            "updated": self.clock.isoformat(), "acceptanceCriteria": list(ac)}
        return self.issues[key]

    def touch(self, key, **fields):
        self.issues[key].update(fields)
        self.issues[key]["updated"] = self.tick()

    async def call(self, tool, args):
        self.calls.append((tool, args))
        if tool == "jira_search_issues":
            if self.fail_search:
                raise SdlcError("TOOL_ERROR", "Jira is down")
            since = dt.datetime.fromisoformat(args["updatedSince"]) if args.get("updatedSince") else None
            rows = sorted((i for i in self.issues.values()
                           if since is None or dt.datetime.fromisoformat(i["updated"]) >= since.replace(second=0)),
                          key=lambda i: i["updated"])
            start = int(args.get("nextPageToken") or 0)
            size = args.get("maxResults", 50)
            page = rows[start:start + size]
            nxt = str(start + size) if start + size < len(rows) else None
            return {"issues": page, "nextPageToken": nxt, "total": len(rows)}
        if tool == "jira_get_issue":
            return {"issue": self.issues[args["key"]]}
        if tool in ("jira_create_epic", "jira_create_story"):
            self.n += 1
            key = f"SHOP-{100 + self.n}"
            is_epic = tool == "jira_create_epic"
            self.add(key, "Epic" if is_epic else "Story", args.get("title") or args["storyText"],
                     ac=args.get("gherkinCriteria", ()), points=args.get("storyPoints"),
                     epic=args.get("epicKey"))
            return {"epicKey": key} if is_epic else {"storyKey": key}
        if tool == "jira_update_issue":
            cur = self.issues[args["key"]]
            if args.get("expectedUpdated") and dt.datetime.fromisoformat(cur["updated"]) != dt.datetime.fromisoformat(args["expectedUpdated"]):
                raise SdlcError("GATE_CONFLICT", "changed in Jira")
            f = args["fields"]
            self.touch(args["key"], summary=f.get("summary", cur["summary"]), description=f.get("description", ""),
                       storyPoints=f.get("storyPoints"), labels=f.get("labels", []),
                       acceptanceCriteria=f.get("acceptanceCriteria", cur["acceptanceCriteria"]))
            return {"issue": self.issues[args["key"]]}
        if tool == "jira_transition_issue":
            cat = {"To Do": "todo", "In Progress": "inprogress", "Done": "done"}[args["toStatus"]]
            self.touch(args["key"], status=args["toStatus"], statusCategory=cat)
            return {"issue": self.issues[args["key"]]}
        raise AssertionError(tool)


@pytest.fixture
async def jx(env):
    e = env
    await e.pg.pool.execute("UPDATE projects SET jira_project_key='SHOP' WHERE id=$1", e.pid)
    e.jira = FakeJira()
    e.sync = JiraSyncService(e.pg, e.jira, e.audit, e.agile)
    e.backlog.sync_hook = e.sync.write_through
    await e.agile.enable(e.pid, e.pm, methodology="scrum")
    return e


async def items(e):
    return {i["jiraKey"] or i["key"]: i for i in (await e.backlog.list(e.pid, e.po))["items"]}


async def test_initial_import_maps_types_status_and_epic_links(jx):
    e = jx
    e.jira.add("SHOP-1", "Epic", "Checkout")
    e.jira.add("SHOP-2", "Story", "Pay by card", points=5, ac=["card accepted"], epic="SHOP-1")
    e.jira.add("SHOP-3", "Bug", "Rounding", cat="done")
    e.jira.add("SHOP-4", "Initiative", "Ignored")
    res = await e.sync.sync(e.pid, e.po)
    assert (res["created"], res["ignored"], res["status"]) == (3, 1, "ok")
    got = await items(e)
    assert got["SHOP-1"]["type"] == "epic" and got["SHOP-2"]["epicKey"] == got["SHOP-1"]["key"]
    assert got["SHOP-2"]["estimate"] == 5.0 and got["SHOP-2"]["status"] == "refined"
    assert got["SHOP-3"]["status"] == "done" and got["SHOP-3"]["type"] == "bug"
    again = await e.sync.sync(e.pid, e.po)                                      # idempotent
    assert (again["created"], again["updated"]) == (0, 0) and len(await items(e)) == 3


async def test_incremental_sync_only_applies_what_changed(jx):
    e = jx
    for i in range(1, 4):
        e.jira.add(f"SHOP-{i}", "Story", f"S{i}", ac=["a"], points=2)
    await e.sync.sync(e.pid, e.po)
    e.jira.touch("SHOP-2", summary="Renamed in Jira", storyPoints=8)
    res = await e.sync.sync(e.pid, e.po)
    assert (res["updated"], res["created"]) == (1, 0) and res["unchanged"] >= 2         # the overlap window re-reads, dedup skips
    got = await items(e)
    assert got["SHOP-2"]["title"] == "Renamed in Jira" and got["SHOP-2"]["estimate"] == 8.0
    state = await e.sync.status(e.pid, e.po)
    assert state["enabled"] and state["lastStatus"] == "ok" and state["watermark"]


async def test_watermark_overlap_covers_timezone_skew(jx):
    e = jx
    e.jira.add("SHOP-1", ac=["a"])
    await e.sync.sync(e.pid, e.po)
    e.jira.calls.clear()
    e.jira.touch("SHOP-1", summary="edited an hour 'earlier' than the watermark due to a timezone offset")
    e.jira.issues["SHOP-1"]["updated"] = (e.jira.clock - dt.timedelta(minutes=30)).isoformat()   # older than the watermark
    await e.sync.sync(e.pid, e.po)
    sent = [a for t, a in e.jira.calls if t == "jira_search_issues"][0]
    wm = dt.datetime.fromisoformat((await e.sync.status(e.pid, e.po))["watermark"])
    assert dt.datetime.fromisoformat(sent["updatedSince"]) <= wm - OVERLAP + dt.timedelta(seconds=1)


async def test_paging_through_a_large_project(jx):
    e = jx
    for i in range(1, 251):
        e.jira.add(f"SHOP-{i}", "Story", f"S{i}", ac=["a"])
    res = await e.sync.sync(e.pid, e.po)
    assert res["created"] == 250 and res["pages"] == 3


async def test_jira_wins_for_jira_owned_fields_and_devmind_keeps_its_own(jx):
    e = jx
    e.jira.add("SHOP-1", "Story", "Original", ac=["a"], points=3)
    await e.sync.sync(e.pid, e.po)
    mine = (await items(e))["SHOP-1"]
    await e.pg.update_backlog_item(e.pid, mine["id"], expected_version=None, components=["web"])   # DevMind-owned
    await e.pg.pool.execute("UPDATE backlog_items SET updated_at = now() + interval '5 minutes' WHERE id=$1", mine["id"])
    ranked = (await items(e))["SHOP-1"]["rank"]
    e.jira.touch("SHOP-1", summary="Edited in Jira")
    res = await e.sync.sync(e.pid, e.po)
    got = (await items(e))["SHOP-1"]
    assert got["title"] == "Edited in Jira" and got["components"] == ["web"] and got["rank"] == ranked
    assert res["conflicts"] == 1                                             # a local change existed → counted


async def test_jira_done_and_reopen_and_the_sprint_commitment_is_never_pulled(jx):
    e = jx
    e.jira.add("SHOP-1", "Story", "A", ac=["a"], points=3)
    await e.sync.sync(e.pid, e.po)
    row = (await items(e))["SHOP-1"]
    v = await e.backlog.set_status(e.pid, e.po, row["key"], "ready", expected_version=row["version"])
    e.jira.touch("SHOP-1", status="In Progress", statusCategory="inprogress")
    await e.sync.sync(e.pid, e.po)
    assert (await items(e))["SHOP-1"]["status"] == "ready"                 # not in a sprint → Jira cannot start it
    e.jira.touch("SHOP-1", status="Done", statusCategory="done")
    await e.sync.sync(e.pid, e.po)
    assert (await items(e))["SHOP-1"]["status"] == "done"
    e.jira.touch("SHOP-1", status="To Do", statusCategory="todo")
    await e.sync.sync(e.pid, e.po)
    assert (await items(e))["SHOP-1"]["status"] == "ready" and v["version"]


async def test_devmind_created_items_are_pushed_once_epic_first(jx):
    e = jx
    epic = await e.backlog.create(e.pid, e.po, {"type": "epic", "title": "Payments"})
    assert [t for t, _ in e.jira.calls] == ["jira_create_epic", "jira_get_issue"]
    epic = await e.backlog.get(e.pid, e.po, epic["key"])
    assert epic["jiraKey"] == "SHOP-101"
    # a story without acceptance criteria is not pushed (the connector requires them)
    s = await e.backlog.create(e.pid, e.po, {"title": "No criteria yet", "epicKey": epic["key"]})
    assert (await e.backlog.get(e.pid, e.po, s["key"]))["jiraKey"] is None
    s = await e.backlog.update(e.pid, e.po, s["key"], {"acceptanceCriteria": ["works"], "estimate": 3}, expected_version=s["version"])
    got = await e.backlog.get(e.pid, e.po, s["key"])
    assert got["jiraKey"] == "SHOP-102"
    created = [a for t, a in e.jira.calls if t == "jira_create_story"]
    assert created[0]["epicKey"] == "SHOP-101" and created[0]["gherkinCriteria"] == ["works"] and created[0]["storyPoints"] == 3
    # a story whose epic is not in Jira yet is held back, not mis-filed
    orphan = await e.backlog.create(e.pid, e.po, {"title": "Orphan", "acceptanceCriteria": ["a"]})
    assert (await e.backlog.get(e.pid, e.po, orphan["key"]))["jiraKey"] is None
    n = len(e.jira.calls)
    await e.sync.sync(e.pid, e.po)                                            # pulling our own pushes creates no duplicates
    assert len(await items(e)) == 3 and not [t for t, _ in e.jira.calls[n:] if t.startswith("jira_create")]


async def test_edits_and_status_changes_are_written_through(jx):
    e = jx
    e.jira.add("SHOP-1", "Story", "A", ac=["a"], points=3)
    await e.sync.sync(e.pid, e.po)
    row = (await items(e))["SHOP-1"]
    await e.backlog.update(e.pid, e.po, row["key"], {"title": "Edited in DevMind", "estimate": 5}, expected_version=row["version"])
    assert e.jira.issues["SHOP-1"]["summary"] == "Edited in DevMind" and e.jira.issues["SHOP-1"]["storyPoints"] == 5
    cur = (await items(e))["SHOP-1"]
    await e.backlog.set_status(e.pid, e.po, cur["key"], "ready", expected_version=cur["version"])
    assert e.jira.issues["SHOP-1"]["statusCategory"] == "todo"                  # ready → To Do (already there)
    n = len([t for t, _ in e.jira.calls if t == "jira_update_issue"])
    res = await e.sync.sync(e.pid, e.po)
    assert res["updated"] == 0 and len([t for t, _ in e.jira.calls if t == "jira_update_issue"]) == n


async def test_write_through_conflict_means_jira_wins(jx):
    e = jx
    e.jira.add("SHOP-1", "Story", "A", ac=["a"], points=3)
    await e.sync.sync(e.pid, e.po)
    row = (await items(e))["SHOP-1"]
    e.jira.touch("SHOP-1", summary="Changed in Jira meanwhile")                 # someone edits Jira first
    await e.backlog.update(e.pid, e.po, row["key"], {"title": "My late edit"}, expected_version=row["version"])
    assert e.jira.issues["SHOP-1"]["summary"] == "Changed in Jira meanwhile"     # not overwritten
    assert (await items(e))["SHOP-1"]["title"] == "Changed in Jira meanwhile"   # and DevMind re-pulled it


async def test_connector_failure_keeps_the_watermark_and_reports(jx):
    e = jx
    e.jira.add("SHOP-1", ac=["a"])
    await e.sync.sync(e.pid, e.po)
    wm = (await e.sync.status(e.pid, e.po))["watermark"]
    e.jira.fail_search = True
    res = await e.sync.sync(e.pid, e.po)
    assert res["status"] == "error" and "Jira is down" in res["errors"][0]
    st = await e.sync.status(e.pid, e.po)
    assert st["lastStatus"] == "error" and st["watermark"] == wm


async def test_one_bad_issue_does_not_stop_the_run(jx):
    e = jx
    e.jira.add("SHOP-1", "Story", "Good", ac=["a"])
    e.jira.add("SHOP-2", "Story", "Bad", ac=["a"])
    e.jira.issues["SHOP-2"]["key"] = None                                       # malformed payload
    res = await e.sync.sync(e.pid, e.po)
    assert res["created"] == 1 and res["status"] == "partial" and res["errors"]


async def test_permissions_and_missing_configuration(jx):
    e = jx
    with pytest.raises(SdlcError) as err:
        await e.sync.sync(e.pid, e.dev)
    assert err.value.code == "FORBIDDEN"
    await e.pg.pool.execute("UPDATE projects SET jira_project_key=NULL WHERE id=$1", e.pid)
    with pytest.raises(SdlcError) as err:
        await e.sync.sync(e.pid, e.po)
    assert "No Jira project key" in err.value.message
    st = await e.sync.status(e.pid, e.dev)                                      # members can see the status
    assert st["enabled"] is False and "integrations" in st["reason"]
    assert await e.pg.list_agile_project_ids() == []


async def test_watermark_never_advances_past_a_failed_issue(jx):
    e = jx
    e.jira.add("SHOP-1", "Story", "Good", ac=["a"])
    e.jira.add("SHOP-2", "Story", "Flaky", ac=["a"])
    e.jira.add("SHOP-3", "Story", "Later", ac=["a"])
    real = e.sync._apply_issue

    async def flaky(pid, issue, res):
        if issue["key"] == "SHOP-2":
            raise RuntimeError("transient db error")
        return await real(pid, issue, res)
    e.sync._apply_issue = flaky
    res = await e.sync.sync(e.pid, e.po)
    assert res["status"] == "partial" and res["created"] == 2
    wm = dt.datetime.fromisoformat((await e.sync.status(e.pid, e.po))["watermark"])
    assert wm < dt.datetime.fromisoformat(e.jira.issues["SHOP-2"]["updated"])          # re-reads SHOP-2 next time
    e.sync._apply_issue = real
    res = await e.sync.sync(e.pid, e.po)
    assert res["created"] == 1 and "SHOP-2" in await items(e)


async def test_unlinked_local_twin_is_adopted_instead_of_duplicated(jx):
    e = jx
    local = await e.backlog.create(e.pid, e.po, {"title": "Pay by  card", "estimate": 3})   # no AC → never pushed
    e.jira.add("SHOP-9", "Story", "pay by card", ac=["accepted"], points=5)
    res = await e.sync.sync(e.pid, e.po)
    assert res["created"] == 0 and res["updated"] == 1
    got = await items(e)
    assert list(got) == ["SHOP-9"] and got["SHOP-9"]["key"] == local["key"] and got["SHOP-9"]["estimate"] == 5.0


async def test_empty_jira_values_do_not_wipe_local_ones(jx):
    e = jx
    e.jira.add("SHOP-1", "Story", "S", ac=["a"], points=3)
    await e.sync.sync(e.pid, e.po)
    e.jira.touch("SHOP-1", acceptanceCriteria=[], storyPoints=None, summary="S2")
    await e.sync.sync(e.pid, e.po)
    got = (await items(e))["SHOP-1"]
    assert got["title"] == "S2" and got["estimate"] == 3.0 and got["acceptanceCriteria"] == ["a"]


async def test_concurrent_sync_and_create_never_duplicates(jx):
    import asyncio
    e = jx
    epic = await e.backlog.create(e.pid, e.po, {"title": "Epic", "type": "epic"})
    await asyncio.gather(e.sync.sync(e.pid, e.po), e.sync.sync(e.pid, e.po))
    await e.sync.sync(e.pid, e.po)
    keys = [i["title"] for i in (await e.backlog.list(e.pid, e.po))["items"]]
    assert keys.count("Epic") == 1 and epic["key"]
