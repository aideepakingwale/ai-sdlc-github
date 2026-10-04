"""The Agile REST API over HTTP: request validation, status codes, error shape and authorisation."""

import httpx
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.agile.index_service import IndexService
from app.agile.jira_sync import JiraSyncService
from app.api import agile_routes
from app.api.deps import Container, current_user
from app.domain.errors import SdlcError
from app.services.content_store import FilesystemContentStore

from .test_jira_sync_pg import FakeJira
from .helpers import _approve, _finish_project_stages

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def client(env, tmp_path):
    e = env
    app = FastAPI()

    @app.exception_handler(SdlcError)
    async def _h(_r, err):  # same mapping as app.main
        return JSONResponse(status_code=err.http_status, content={"error": {"code": err.code, "message": err.message}})

    @app.exception_handler(RequestValidationError)
    async def _v(_r, err):
        return JSONResponse(status_code=400, content={"error": {"code": "VALIDATION_FAILED", "message": str(err.errors()[0]["loc"])}})

    content = FilesystemContentStore(str(tmp_path))
    index = IndexService(e.pg, content, e.audit, None, e.wf)
    jira = JiraSyncService(e.pg, FakeJira(), e.audit, e.agile)
    e.container = Container(settings=None, db=e.pg, authz=e.authz, agile=e.agile,
                            extras={"backlog": e.backlog, "proposals": e.proposals, "index": index, "jira": jira})
    app.state.container = e.container
    app.include_router(agile_routes.router)
    e.who = {"user": e.pm}
    app.dependency_overrides[current_user] = lambda: e.who["user"]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        e.http = c
        yield e


def url(e, path=""):
    return f"/api/projects/{e.pid}/agile{path}"


async def test_overview_before_and_after_enabling(client):
    e = client
    r = await e.http.get(url(e))
    assert r.status_code == 200 and r.json() == {"enabled": False, "methodology": "waterfall",
                                                  "permissions": {"canManage": True, "canRun": True}}
    r = await e.http.post(url(e, "/enable"), json={"methodology": "scrum", "sprintDays": 10, "defaultCapacity": 25})
    assert r.status_code == 200 and r.json()["settings"]["sprintDays"] == 10 and r.json()["currentRelease"]["code"] == "R-001"
    again = await e.http.post(url(e, "/enable"), json={"methodology": "kanban"})
    assert again.status_code == 409 and again.json()["error"]["code"] == "GATE_CONFLICT"


@pytest.mark.parametrize("body", [
    {"methodology": "waterfall"}, {"methodology": "scrum", "sprintDays": 0}, {"methodology": "scrum", "sprintDays": 99},
    {"methodology": "scrum", "defaultCapacity": -1}, {"methodology": "scrum", "indexStrategy": "elsewhere"},
    {"methodology": "scrum", "autoMinScore": 101}, {}])
async def test_enable_rejects_bad_input(client, body):
    r = await client.http.post(url(client, "/enable"), json=body)
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_authorisation_over_http(client):
    e = client
    await e.http.post(url(e, "/enable"), json={"methodology": "scrum"})
    e.who["user"] = e.stranger                                              # not a member of the project
    for method, path, body in (("GET", "", None), ("GET", "/backlog", None), ("GET", "/index", None), ("GET", "/jira", None),
                               ("GET", "/proposals?phase=1&kind=plan", None)):
        r = await e.http.request(method, url(e, path))
        assert r.status_code == 403, (method, path, r.status_code)
    for method, path, body in (("POST", "/sprints", {"goal": "x"}), ("POST", "/backlog", {"title": "x"}),
                               ("POST", "/enable", {"methodology": "scrum"}), ("PATCH", "/settings", {"sprintDays": 7}),
                               ("POST", "/jira/sync", {})):
        r = await e.http.request(method, url(e, path), json=body)
        assert r.status_code == 403, (method, path, r.status_code)
    e.who["user"] = e.dev                                                   # a developer: may read, may not run ceremonies
    assert (await e.http.get(url(e, "/backlog"))).status_code == 200
    assert (await e.http.post(url(e, "/backlog"), json={"title": "x"})).status_code == 403
    assert (await e.http.post(url(e, "/sprints"), json={})).status_code == 403
    assert (await e.http.patch(url(e, "/settings"), json={"sprintDays": 7})).status_code == 403
    ov = (await e.http.get(url(e))).json()
    assert ov["permissions"] == {"canManage": False, "canRun": False}


async def test_cross_project_ids_are_not_reachable(client):
    e = client
    await e.http.post(url(e, "/enable"), json={"methodology": "scrum"})
    await e.pg.pool.execute("INSERT INTO projects (id, name, created_by) VALUES ('other','Other',$1)", e.pm.id)
    await e.pg.add_member(project_id="other", user_id=e.po.id, role="PO", added_by=e.pm.id)
    mine = (await e.http.post(url(e, "/backlog"), json={"title": "Mine"})).json()
    r = await e.http.get(f"/api/projects/other/agile/backlog/{mine['key']}")
    assert r.status_code == 404                                              # same key space, other project: not found
    r = await e.http.post(f"/api/projects/other/agile/sprints/{'nope'}/cancel")
    assert r.status_code in (400, 404, 409)


async def test_backlog_crud_status_and_concurrency(client):
    e = client
    await e.http.post(url(e, "/enable"), json={"methodology": "scrum"})
    r = await e.http.post(url(e, "/backlog"), json={"title": "Pay by card", "acceptanceCriteria": ["card works"], "estimate": 5})
    item = r.json()
    assert r.status_code == 200 and item["key"] == "DM-1" and item["status"] == "refined" and item["version"] == 1
    assert (await e.http.post(url(e, "/backlog"), json={"title": "x", "estimate": 2.3})).status_code == 400
    assert (await e.http.post(url(e, "/backlog"), json={"title": ""})).status_code == 400
    assert (await e.http.post(url(e, "/backlog"), json={"title": "x" * 201})).status_code == 400
    r = await e.http.patch(url(e, "/backlog/DM-1"), json={"title": "Pay by card v2", "expectedVersion": 1})
    assert r.status_code == 200 and r.json()["version"] == 2
    r = await e.http.patch(url(e, "/backlog/DM-1"), json={"title": "stale", "expectedVersion": 1})
    assert r.status_code == 409 and "someone else" in r.json()["error"]["message"]
    r = await e.http.patch(url(e, "/backlog/DM-1"), json={"clearEstimate": True})
    assert r.json()["estimate"] is None and any("estimate" in p for p in r.json()["problems"])
    assert (await e.http.patch(url(e, "/backlog/DM-1"), json={})).status_code == 400            # nothing to change
    r = await e.http.post(url(e, "/backlog/DM-1/status"), json={"status": "done"})
    assert r.status_code == 409 and "cannot move" in r.json()["error"]["message"]
    assert (await e.http.post(url(e, "/backlog/DM-1/status"), json={"status": "bogus"})).status_code == 400
    assert (await e.http.get(url(e, "/backlog/DM-404"))).status_code == 404
    lst = (await e.http.get(url(e, "/backlog?status=refined,ready&q=card"))).json()
    assert [i["key"] for i in lst["items"]] == ["DM-1"] and lst["summary"]["count"] == 1
    assert (await e.http.get(url(e, "/backlog?status=nonsense"))).status_code == 400


async def test_sprint_lifecycle_and_proposals_over_http(client):
    e = client
    await e.http.post(url(e, "/enable"), json={"methodology": "scrum"})
    await _finish_project_stages(e)
    r = await e.http.post(url(e, "/sprints"), json={"goal": "Checkout", "capacity": 12, "startsOn": "2026-02-02"})
    s = r.json()
    assert r.status_code == 200 and s["label"] == "S-001" and s["endsOn"] == "2026-02-16" and s["capacity"] == 12
    assert (await e.http.post(url(e, "/sprints"), json={})).status_code == 409                     # one open sprint
    assert (await e.http.post(url(e, "/sprints"), json={"goal": "x" * 241})).status_code == 400
    assert (await e.http.get(url(e, "/proposals?phase=3&kind=refine"))).json() == {"proposal": None}
    assert (await e.http.get(url(e, "/proposals?phase=3&kind=bogus"))).status_code == 400
    r = await e.http.patch(url(e, "/settings"), json={"clearWipLimit": True, "autoMinScore": 90})
    assert r.status_code == 200 and r.json()["settings"]["autoMinScore"] == 90 and r.json()["settings"]["wipLimit"] is None
    assert (await e.http.patch(url(e, "/settings"), json={})).status_code == 400
    r = await e.http.post(url(e, f"/sprints/{s['id']}/cancel"))
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    r = await e.http.post(url(e, "/releases/%s/harden" % (await e.http.get(url(e))).json()["currentRelease"]["id"]))
    assert r.status_code == 409 and "no completed sprint" in r.json()["error"]["message"]


async def test_index_and_jira_status_endpoints(client):
    e = client
    await e.http.post(url(e, "/enable"), json={"methodology": "scrum"})
    assert (await e.http.get(url(e, "/index"))).json() == {"initialised": False}
    j = (await e.http.get(url(e, "/jira"))).json()
    assert j["enabled"] is False and "integrations" in j["reason"]
    r = await e.http.post(url(e, "/jira/sync"), json={})
    assert r.status_code == 400 and "No Jira project key" in r.json()["error"]["message"]
