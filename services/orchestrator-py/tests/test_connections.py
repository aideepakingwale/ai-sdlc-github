"""Per-project connections: encrypted write-only credentials, a real test for each system, and the project's own knowledge-base switches."""
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from app.domain.errors import SdlcError
from app.domain.models import UserPublic
from app.services.connections import ConnectionService, host_is_safe
from app.services.rag import RagService


def user(role="PROJECT_MANAGER", uid="u1"):
    return UserPublic(id=uid, email=f"{uid}@x.io", displayName=uid, role=role)


class Db:
    def __init__(self):
        self.rows: dict[tuple[str, str], dict] = {}
        self.project = {"id": "p1", "created_by": "u1", "github_repo": None, "atlassian_site_url": None,
                        "jira_project_key": None, "confluence_space_key": None}

    async def get_project(self, pid):
        return self.project

    async def update_project_integrations(self, pid, i):
        self.project.update(github_repo=i["githubRepo"], atlassian_site_url=i["atlassianSiteUrl"],
                            jira_project_key=i["jiraProjectKey"], confluence_space_key=i["confluenceSpaceKey"])

    async def list_connections(self, pid):
        return [r for (p, _), r in self.rows.items() if p == pid]

    async def get_connection(self, pid, kind):
        return self.rows.get((pid, kind))

    async def upsert_connection(self, pid, kind, settings, secret, by):
        self.rows[(pid, kind)] = {"project_id": pid, "kind": kind, "settings": settings, "secret_enc": secret, "last_test": None,
                                  "updated_by": by, "updated_at": datetime.now(UTC)}

    async def set_connection_test(self, pid, kind, result):
        self.rows[(pid, kind)]["last_test"] = result

    async def count_kb_docs(self, scopes):
        return [{"scope": "global", "source": "standard", "n": 3}, {"scope": "p1", "source": "artifact", "n": 2}]


class Authz:
    async def assert_project_access(self, pid, u):
        return None


class Audit:
    def __init__(self):
        self.events = []

    def record(self, **k):
        self.events.append(k)


SETTINGS = SimpleNamespace(JWT_SECRET="x" * 40, CONNECTIONS_KEY=None, CONNECTIONS_ALLOW_PRIVATE_HOSTS=True)


def svc(handler=None):
    db, audit = Db(), Audit()
    transport = httpx.MockTransport(handler or (lambda r: httpx.Response(404)))
    return ConnectionService(db, Authz(), audit, SETTINGS, None, transport), db, audit


@pytest.mark.asyncio
async def test_the_credential_is_encrypted_and_never_returned():
    s, db, audit = svc()
    out = await s.save("p1", "github", user(), {"settings": {"repo": "acme/pay"}, "secrets": {"token": "ghp_SECRET123"}})
    assert out["secretSet"] is True and "ghp_SECRET123" not in str(out)
    assert "ghp_SECRET123" not in str(db.rows[("p1", "github")])                       # sealed at rest
    assert "ghp_SECRET123" not in str(audit.events)
    assert "ghp_SECRET123" not in str(await s.view("p1", user()))
    assert db.project["github_repo"] == "acme/pay"                                   # the publishing target follows
    assert (await s.credentials_for("p1"))["github"]["token"] == "ghp_SECRET123"     # but the tools can use it


@pytest.mark.asyncio
async def test_saving_without_a_token_keeps_the_stored_one_and_clear_removes_it():
    s, db, _ = svc()
    await s.save("p1", "github", user(), {"settings": {"repo": "acme/pay"}, "secrets": {"token": "t1"}})
    await s.save("p1", "github", user(), {"settings": {"repo": "acme/pay2"}})
    assert (await s.view("p1", user()))["connections"]["github"]["secretSet"] is True
    await s.save("p1", "github", user(), {"settings": {"repo": "acme/pay2"}, "clearSecret": True})
    assert (await s.view("p1", user()))["connections"]["github"]["secretSet"] is False


@pytest.mark.asyncio
async def test_only_the_managing_pm_or_admin_can_change_or_test():
    s, _, _ = svc()
    for fn in (lambda: s.save("p1", "jira", user("DEV", "u2"), {"settings": {}}), lambda: s.test("p1", "jira", user("DEV", "u2"))):
        with pytest.raises(SdlcError) as e:
            await fn()
        assert e.value.code == "FORBIDDEN"
    assert (await s.view("p1", user("DEV", "u2")))["canEdit"] is False


@pytest.mark.asyncio
async def test_bad_values_are_rejected():
    s, _, _ = svc()
    for kind, settings in (("github", {"repo": "not a repo"}), ("jira", {"projectKey": "pay!"}), ("jira", {"baseUrl": "ftp://x"})):
        with pytest.raises(SdlcError):
            await s.save("p1", kind, user(), {"settings": settings})


def test_private_addresses_are_refused_unless_allowed():
    assert host_is_safe("http://127.0.0.1:8080", False)
    assert host_is_safe("https://169.254.169.254/latest", False)
    assert host_is_safe("https://user:pw@example.com", False)
    assert host_is_safe("http://jira.internal", True) is None


@pytest.mark.asyncio
async def test_github_test_reports_each_check():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "Bearer tok"
        if req.url.path == "/user":
            return httpx.Response(200, json={"login": "octo"})
        if req.url.path == "/repos/acme/pay":
            return httpx.Response(200, json={"permissions": {"push": False}, "default_branch": "main"})
        return httpx.Response(200, json={})
    s, _, _ = svc(handler)
    r = await s.test("p1", "github", user(), {"settings": {"repo": "acme/pay"}, "secrets": {"token": "tok"}})
    by = {c["name"]: c for c in r["checks"]}
    assert by["Token accepted"]["ok"] and by["Repository reachable"]["ok"]
    assert by["Can write to it"]["ok"] is False and r["ok"] is False


@pytest.mark.asyncio
async def test_a_refused_token_stops_at_the_first_check():
    s, _, _ = svc(lambda r: httpx.Response(401))
    r = await s.test("p1", "github", user(), {"settings": {"repo": "acme/pay"}, "secrets": {"token": "bad"}})
    assert [c["name"] for c in r["checks"]] == ["Token accepted"] and r["checks"][0]["detail"] == "The credentials were refused"


@pytest.mark.asyncio
async def test_without_an_own_credential_it_says_so_instead_of_pretending():
    s, _, _ = svc()
    r = await s.test("p1", "github", user(), {"settings": {"repo": "acme/pay"}})
    assert r["usesOwnCredential"] is False and r["ok"] is True
    assert any(c["name"] == "Own access token" and c.get("optional") for c in r["checks"])


@pytest.mark.asyncio
async def test_jira_and_confluence_share_credentials_when_asked():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        if req.url.path.endswith("/myself") or req.url.path.endswith("/user/current"):
            return httpx.Response(200, json={"displayName": "Dee"})
        if "/mypermissions" in req.url.path or req.url.path.endswith("/mypermissions"):
            return httpx.Response(200, json={"permissions": {"CREATE_ISSUES": {"havePermission": True}}})
        return httpx.Response(200, json={"name": "Payments"})
    s, _, _ = svc(handler)
    await s.save("p1", "jira", user(), {"settings": {"baseUrl": "https://acme.atlassian.net", "projectKey": "PAY", "email": "d@acme.io"},
                                        "secrets": {"apiToken": "jt"}})
    j = await s.test("p1", "jira", user())
    assert j["ok"] and [c["name"] for c in j["checks"]] == ["Credentials accepted", "Project PAY found", "Can create issues"]
    await s.save("p1", "confluence", user(), {"settings": {"baseUrl": "https://acme.atlassian.net", "spaceKey": "PAYDOCS", "sameAsJira": True}})
    c = await s.test("p1", "confluence", user())
    assert c["ok"], c
    assert (await s.credentials_for("p1"))["confluence"]["apiToken"] == "jt"


@pytest.mark.asyncio
async def test_kb_counts_and_the_project_switches_filter_retrieval():
    s, db, _ = svc()
    r = await s.test("p1", "kb", user())
    assert {c["name"]: c["detail"] for c in r["checks"]}["Organisation standards"] == "3 indexed"

    class RDb:
        async def get_connection(self, pid, kind):
            return {"settings": {"standards": False, "topK": 1}}

        async def fetch_kb_docs(self, scopes):
            e = [1.0] * 8
            return [{"id": "a", "source": "standard", "title": "Std", "content": "queue", "embedding": e},
                    {"id": "b", "source": "artifact", "title": "[P1] X: y", "content": "queue", "embedding": e},
                    {"id": "c", "source": "codebase", "title": "[code] q", "content": "queue", "embedding": e}]
    rag = RagService(RDb(), SimpleNamespace(RAG_EMBED_DIM=8, RAG_TOP_K=4))
    rag.embedder = SimpleNamespace(embed=lambda t: [1.0] * 8)
    got = await rag.retrieve("queue", "p1")
    assert len(got) == 1 and got[0]["source"] != "standard"
