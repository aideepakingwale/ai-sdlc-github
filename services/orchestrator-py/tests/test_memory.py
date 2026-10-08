"""Memory: suggested from what people do, confirmed by a person, then (and only then) given to the agents."""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.domain.errors import SdlcError
from app.domain.models import UserPublic
from app.services.context_manifest import build_manifest
from app.services.memory import MemoryService, fingerprint, style_cues


def user(role="PROJECT_MANAGER", uid="u1"):
    return UserPublic(id=uid, email=f"{uid}@x.io", displayName=uid, role=role)


class Db:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.n = 0

    async def get_project(self, pid):
        return {"id": pid, "created_by": "u1"}

    async def list_memory(self, pid, uid):
        return [r for r in self.rows.values() if (r["scope"] == "project" and r["project_id"] == pid)
                or r["scope"] == "org" or (r["scope"] == "user" and r["owner_id"] == uid)]

    async def get_memory(self, mid):
        return self.rows.get(mid)

    async def insert_memory(self, **k):
        if any(r["fingerprint"] == k["fingerprint"] and r["scope"] == k["scope"] and r["project_id"] == k["project_id"]
               and r["owner_id"] == k["owner_id"] for r in self.rows.values()):
            return None
        self.n += 1
        now = datetime.now(UTC)
        row = {**k, "id": f"m{self.n}", "uses": 0, "last_used_at": None, "created_at": now, "updated_at": now}
        self.rows[row["id"]] = row
        return row

    async def update_memory(self, mid, patch):
        self.rows[mid].update(patch)
        return self.rows[mid]

    async def delete_memory(self, mid):
        return self.rows.pop(mid, None) is not None

    async def touch_memory(self, ids):
        for i in ids:
            self.rows[i]["uses"] += 1


class Authz:
    def __init__(self, role=None):
        self.role = role

    async def assert_project_access(self, pid, u):
        return None

    async def get_membership_role(self, pid, uid):
        return self.role


class Audit:
    def __init__(self):
        self.events = []

    def record(self, **k):
        self.events.append(k["event"])


def svc(role=None):
    db = Db()
    return MemoryService(db, Authz(role), Audit()), db


@pytest.mark.asyncio
async def test_a_curator_adds_active_and_anyone_else_only_suggests():
    s, _ = svc()
    mine = await s.create("p1", user(), {"title": "Queues use SQS FIFO", "body": "Name them <env>-<service>-q"})
    assert mine["status"] == "active"
    other = await s.create("p1", user("DEV", "u2"), {"title": "Prefer PostgreSQL", "body": "Not MySQL"})
    assert other["status"] == "suggested"


@pytest.mark.asyncio
async def test_only_confirmed_memory_reaches_the_prompt():
    s, db = svc()
    await s.suggest(project_id="p1", kind="decision", title="Use SQS", body="FIFO queues", source={"type": "clarification"})
    assert (await s.block_for("p1", "u1", 1))[0] == ""          # suggested: not used
    mid = next(iter(db.rows))
    await s.update("p1", mid, user(), {"status": "active"})
    block, used = await s.block_for("p1", "u1", 1, record_use=True)
    assert "Use SQS" in block and used[0]["id"] == mid
    assert db.rows[mid]["uses"] == 1
    await s.update("p1", mid, user(), {"status": "rejected"})
    assert (await s.block_for("p1", "u1", 1))[0] == ""


@pytest.mark.asyncio
async def test_a_repeat_or_rejected_suggestion_is_not_proposed_again():
    s, db = svc()
    a = await s.suggest(project_id="p1", kind="decision", title="Use SQS", body="FIFO", source={})
    assert a and await s.suggest(project_id="p1", kind="decision", title="Use SQS", body="FIFO", source={}) is None
    await s.update("p1", a["id"], user(), {"status": "rejected"})
    assert await s.suggest(project_id="p1", kind="decision", title="Use SQS", body="FIFO", source={}) is None
    assert len(db.rows) == 1


@pytest.mark.asyncio
async def test_stage_scope_and_relevance():
    s, db = svc()
    for t, stage in (("Stage one rule", 1), ("Stage two rule", 2), ("Everywhere rule", None)):
        await s.create("p1", user(), {"title": t, "body": f"{t} body about caching", "stage": stage})
    titles = {m["title"] for m in await s.select("p1", "u1", 2)}
    assert titles == {"Stage two rule", "Everywhere rule"}


@pytest.mark.asyncio
async def test_promote_shares_with_other_projects_but_needs_acceptance_first():
    s, db = svc()
    m = await s.suggest(project_id="p1", kind="convention", title="Tag every resource", body="owner + env", source={})
    with pytest.raises(SdlcError):
        await s.promote("p1", m["id"], user())
    await s.update("p1", m["id"], user(), {"status": "active"})
    await s.promote("p1", m["id"], user())
    assert "Tag every resource" in (await s.block_for("p2", "u9", 1))[0]       # another project, another person


@pytest.mark.asyncio
async def test_members_without_rights_cannot_curate_and_user_memory_is_private():
    s, db = svc()
    m = await s.suggest(project_id="p1", kind="lesson", title="Add retries", body="always", source={})
    with pytest.raises(SdlcError) as e:
        await s.update("p1", m["id"], user("DEV", "u2"), {"status": "active"})
    assert e.value.code == "FORBIDDEN"
    mine = await s.create("p1", user("DEV", "u2"), {"title": "Short answers", "body": "Keep summaries brief", "kind": "working_style", "scope": "user"})
    assert mine["status"] == "active"
    assert "Short answers" in (await s.block_for("p1", "u2", 1))[0]
    assert "Short answers" not in (await s.block_for("p1", "u3", 1))[0]
    with pytest.raises(SdlcError):
        await s.delete("p1", mine["id"], user("PROJECT_MANAGER", "u1"))


def test_style_cues_find_standing_preferences_only():
    got = style_cues("Please add a diagram here. Always include a retry section in designs! Rename the title.")
    assert got == ["Always include a retry section in designs!"]


def test_fingerprint_ignores_case_and_spacing():
    assert fingerprint("Use  SQS", "FIFO") == fingerprint("use sqs", "fifo")


def test_manifest_gets_a_memory_layer():
    stage = {"name": "Architecture", "template": 2, "outputs": []}
    m = build_manifest(mode="preview", phase=2, stage=stage, project={}, overlay={}, context_artifacts=[], snippets=[],
                       canon_block="", formworks=[], attached=[], traits=None, has_codebase=False,
                       memories=[{"id": "m1", "title": "Use SQS", "body": "FIFO", "kind": "decision",
                                  "scope": "project"}])
    layer = next(layer for layer in m["layers"] if layer["id"] == "memory")
    assert [i["label"] for i in layer["items"]] == ["Use SQS"]
    assert any(e["from"] == "memory:memory:m1" for e in m["edges"])


@pytest.mark.asyncio
async def test_answers_and_change_requests_become_suggestions_not_active_memory():
    from app.services.chat import ChatService
    from app.services.gates import GateService

    s, db = svc()
    chat = ChatService.__new__(ChatService)
    chat._deps = SimpleNamespace(memory=s)
    await chat._suggest_from_answers("p1", 2, {"name": "Solution Architecture", "template": 2}, user(), [
        {"question": "Which queue should we use?", "answer": "SQS FIFO"},
        {"question": "Anything else?", "answer": ""},                      # unanswered: nothing to remember
    ])
    assert [(r["kind"], r["status"], r["title"], r["body"]) for r in db.rows.values()] == [
        ("decision", "suggested", "Which queue should we use", "SQS FIFO")]

    gates = GateService.__new__(GateService)
    gates.memory = s
    await gates._suggest_from_changes("p1", 2, {"name": "Solution Architecture", "template": 2}, user(),
                                      "Add a data-flow diagram. Always include a retry section in designs.")
    kinds = sorted((r["kind"], r["scope"], r["status"]) for r in db.rows.values())
    assert ("lesson", "project", "suggested") in kinds
    assert ("working_style", "user", "suggested") in kinds
    assert not any(r["status"] == "active" for r in db.rows.values())
