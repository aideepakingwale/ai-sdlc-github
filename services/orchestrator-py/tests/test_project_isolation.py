"""One project's data must never reach, change or be read through another project.

Unit tests use fakes for the route/service guards; the SQL scoping is verified on a REAL Postgres when
TEST_DATABASE_URL is set (admin DSN), otherwise those tests are skipped."""

from __future__ import annotations

import os
import types
import uuid
from pathlib import Path

import pytest

from app.api import project_routes as pr
from app.domain.errors import SdlcError
from app.domain.models import UserPublic
from app.services.authz import AuthzService
from app.services.chat import ChatService
from app.services.gates import GateService
from app.services.workflow import WorkflowService


def user(uid, role="DEV"):
    return UserPublic(id=uid, email=f"{uid}@x.io", displayName=uid, role=role)


class Db:
    """Projects A and B. alice is a member of A only; bob of B only."""

    def __init__(self):
        self.projects = {p: {"id": p, "created_by": f"pm{p}", "current_phase": 1, "status": "ACTIVE", "name": p} for p in "AB"}
        self.members = {("A", "alice"): "DEV", ("B", "bob"): "DEV"}
        self.attachments = {
            "attA": {"id": "attA", "project_id": "A", "storage_key": "c/A/x", "is_text": True, "filename": "a.txt"},
            "attB": {"id": "attB", "project_id": "B", "storage_key": "c/B/x", "is_text": True, "filename": "b.txt"},
        }
        self.artefacts = {
            "artA": {"id": "artA", "project_id": "A", "phase": 1, "type": "PRD", "title": "PRD-A", "content": "A-SECRET", "storage_key": None},
            "artB": {"id": "artB", "project_id": "B", "phase": 1, "type": "PRD", "title": "PRD-B", "content": "B-SECRET", "storage_key": None},
        }
        self.formworks = {
            "fwB": {"id": "fwB", "project_id": "B", "name": "B-template", "template": "B-FORMWORK-BODY"},
            "fwP": {"id": "fwP", "project_id": None, "name": "platform", "template": "PLATFORM"},
        }
        self.deleted: list[tuple[str, str]] = []
        self.read: list[tuple[str, str, str]] = []

    async def get_project(self, pid): return self.projects.get(pid)
    async def get_membership_role(self, pid, uid): return self.members.get((pid, uid))
    async def get_workflow(self, pid): return None
    async def list_members(self, pid): return []
    async def get_attachments_by_ids(self, ids): return [self.attachments[i] for i in ids if i in self.attachments]
    async def get_artefacts_by_ids(self, ids): return [self.artefacts[i] for i in ids if i in self.artefacts]
    async def get_formworks_by_ids(self, ids): return [self.formworks[i] for i in ids if i in self.formworks]

    async def delete_attachment(self, aid, project_id):          # mirrors the scoped SQL
        self.deleted.append((aid, project_id))
        row = self.attachments.get(aid)
        if row and row["project_id"] == project_id:
            return self.attachments.pop(aid)
        return None

    async def mark_notification_read(self, nid, uid, project_id):
        self.read.append((nid, uid, project_id))


class Dynamo:
    async def list_phase_states(self, pid): return []
    async def get_phase_state(self, pid, ph): return None


class Content:
    def __init__(self): self.store = {"c/A/x": "A-ATTACHMENT", "c/B/x": "B-ATTACHMENT-TEXT"}
    async def get(self, k): return self.store.get(k)
    async def put(self, k, v): self.store[k] = v


class Audit:
    def record(self, **k): pass


def container():
    db, content = Db(), Content()
    authz = AuthzService(db)
    wf = WorkflowService(db, Dynamo(), Audit())

    async def idle(*_a): return None
    return types.SimpleNamespace(
        db=db, content=content, authz=authz, gates=GateService(db, Dynamo(), Audit(), authz, wf, None, None),
        chat=types.SimpleNamespace(assert_not_generating=idle))


async def test_a_non_member_cannot_read_another_projects_gate_states():
    c = container()
    with pytest.raises(SdlcError) as err:
        await pr.gate_states("B", user("alice"), c)
    assert err.value.code == "FORBIDDEN"
    assert (await pr.gate_states("A", user("alice"), c))["states"]


async def test_deleting_an_attachment_is_scoped_to_the_project_in_the_url():
    c = container()
    await pr.delete_attachment("A", 1, "attB", user("alice"), c)          # B's id under A's URL
    assert "attB" in c.db.attachments and c.content.store["c/B/x"] == "B-ATTACHMENT-TEXT"   # nothing deleted, nothing blanked
    await pr.delete_attachment("A", 1, "attA", user("alice"), c)
    assert "attA" not in c.db.attachments                                  # its own attachment still works


async def test_marking_a_notification_read_is_scoped_to_the_project():
    c = container()
    await pr.read_notification("A", "nB", user("alice"), c)
    assert c.db.read == [("nB", "alice", "A")]                             # the project filter is applied in SQL


async def test_a_plan_cannot_reference_another_projects_items():
    c = container()
    svc = ChatService.__new__(ChatService)
    svc._db = c.db
    for overlay in ({"referencedArtifactIds": ["artA", "artB"]}, {"attachmentIds": ["attB"]}, {"formworkIds": ["fwB"]}):
        with pytest.raises(SdlcError) as err:
            await svc._assert_own_references("A", overlay)
        assert err.value.code == "VALIDATION_FAILED" and "another project" in err.value.message
    await svc._assert_own_references("A", {"referencedArtifactIds": ["artA", "ghost"], "attachmentIds": ["attA"], "formworkIds": ["fwP"]})


async def test_extra_context_never_includes_another_projects_items():
    c = container()
    svc = ChatService.__new__(ChatService)
    svc._db, svc._deps = c.db, types.SimpleNamespace(content=c.content)
    out = await svc._resolve_extra_context("A", ["artB", "artA"], ["attB", "attA"], ["fwB", "fwP"], lambda e: None)
    assert "B-SECRET" not in out and "B-ATTACHMENT-TEXT" not in out and "B-FORMWORK-BODY" not in out
    assert "A-SECRET" in out and "PLATFORM" in out


# ---------------------------------------------------------------- real Postgres: the SQL itself
ADMIN = os.environ.get("TEST_DATABASE_URL")
needs_pg = pytest.mark.skipif(not ADMIN, reason="TEST_DATABASE_URL not set (real-Postgres isolation tests)")
MIGRATIONS = Path(__file__).resolve().parents[3] / "infra" / "migrations"


@pytest.fixture
async def pg():
    import asyncpg

    from app.repos.pg import Database
    name = f"iso_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(ADMIN)
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    db = Database(f"{ADMIN.rpartition('/')[0]}/{name}")
    await db.connect()
    try:
        await db.run_migrations(MIGRATIONS)
        await db.pool.execute("INSERT INTO users (id,email,display_name,role,password_hash) VALUES ('u','u@t','U','PROJECT_MANAGER','x')")
        yield db
    finally:
        await db.close()
        admin = await asyncpg.connect(ADMIN)
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


@needs_pg
async def test_sql_scopes_attachments_notifications_and_the_retrieval_corpus_to_their_project(pg):
    a = await pg.create_project(name="A", created_by="u")
    b = await pg.create_project(name="B", created_by="u")
    att = await pg.insert_attachment(project_id=b["id"], phase=1, filename="b.txt", content_type="text/plain",
                                     size_bytes=1, is_text=True, storage_key="k", created_by="u")
    assert await pg.delete_attachment(att, a["id"]) is None                 # A's URL cannot delete B's attachment
    assert await pg.get_attachment(att) is not None
    assert await pg.delete_attachment(att, b["id"]) is not None
    nid = await pg.insert_notification(project_id=b["id"], phase=1, kind="stage_ready", title="t")
    await pg.mark_notification_read(nid, "u", a["id"])
    assert (await pg.list_notifications(b["id"]))[0]["read_by"] == []       # not marked through A
    await pg.mark_notification_read(nid, "u", b["id"])
    assert (await pg.list_notifications(b["id"]))[0]["read_by"] == ["u"]
    # retrieval corpus is per project, and deleting a project purges it
    await pg.upsert_kb_doc(doc_id="art-1", scope=b["id"], source="artifact", title="t", content="B only", embedding=[0.1])
    await pg.upsert_kb_doc(doc_id="std-1", scope="global", source="standard", title="s", content="shared", embedding=[0.1])
    assert {d["id"] for d in await pg.fetch_kb_docs(["global", a["id"]])} == {"std-1"}      # B's docs are invisible to A
    assert await pg.delete_project(b["id"])
    assert {d["id"] for d in await pg.fetch_kb_docs(["global", b["id"]])} == {"std-1"}      # …and gone after B is deleted


# ---------------------------------------------------------------- publishing targets are per project
async def test_publishing_tools_carry_their_projects_own_target():
    from app.integrations.mcp_client import McpServer, McpToolClient

    client = McpToolClient("http://tools")
    targets = {"A": {"githubRepo": "acme/a", "jiraProjectKey": "AAA"}, "B": {"githubRepo": "acme/b"}, "C": {}}

    async def target_for(pid): return targets[pid]
    current = {"p": None}
    client.bind_targets(target_for, lambda: current["p"])
    primary = client._servers[0]

    out = await client._with_target(primary, "github_commit_code", {"branch": "main"}, "A")
    assert out["target"] == {"githubRepo": "acme/a", "jiraProjectKey": "AAA"}
    assert (await client._with_target(primary, "github_commit_code", {"branch": "main"}, "B"))["target"] == {"githubRepo": "acme/b"}
    current["p"] = "A"                                                       # the run in progress supplies the project
    assert (await client._with_target(primary, "confluence_publish_prd", {}, None))["target"]["githubRepo"] == "acme/a"
    assert "target" not in await client._with_target(primary, "github_commit_code", {}, "C")        # nothing configured: platform default
    assert "target" not in await client._with_target(primary, "spectral_lint_openapi", {}, "A")      # not a publishing tool
    external = McpServer("gh", "http://x", prefix="github")
    assert "target" not in await client._with_target(external, "create_pull_request", {}, "A")       # never sent to an external server
    assert (await client._with_target(primary, "github_commit_code", {"target": {"githubRepo": "x/y"}}, "A"))["target"] == {"githubRepo": "x/y"}
