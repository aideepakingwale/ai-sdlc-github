"""The gate at the HTTP edge: work-starting routes refuse with a clear 402 while tokens are used up; everything else stays open."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import JSONResponse

from app.api.deps import Container, current_user, require_tokens
from app.auth.sessions import SESSION_COOKIE, sign_session
from app.domain.errors import SdlcError
from app.domain.models import UserPublic
from app.services import usage_context as uc
from app.services.token_gate import TokenGate

SECRET = "s" * 40
ANN = UserPublic(id="ann", email="ann@t", displayName="Ann", role="DEV")  # type: ignore[arg-type]


class FakeRepo:
    def __init__(self):
        self.pools = {"org": None, "project": None}
        self.blocks: list[dict] = []
        self.fail = False

    async def gate_state(self, project_id):
        if self.fail:
            raise RuntimeError("db down")
        return self.pools

    async def record_block(self, **kw):
        self.blocks.append(kw)


def pool(balance):
    return {"id": "x", "metered": True, "allocated_tokens": 100, "consumed_tokens": 100 - balance, "balance_tokens": balance}


@pytest.fixture
def client_and_repo():
    repo = FakeRepo()
    router = APIRouter()
    ran: list[str] = []

    @router.post("/api/projects/{project_id}/work", dependencies=[require_tokens("work")])
    async def work(project_id: str) -> dict:
        ran.append(project_id)
        return {"ran": True, "admitted": uc.current().admitted}

    @router.post("/api/design/test", dependencies=[require_tokens("design.test")])
    async def design() -> dict:
        ran.append(f"design:{uc.current().project_id}")
        return {"ran": True}

    @router.get("/api/projects/{project_id}/read")
    async def read(project_id: str, _u: UserPublic = Depends(current_user)) -> dict:
        return {"read": True}

    app = FastAPI()
    app.include_router(router)

    @app.exception_handler(SdlcError)
    async def handler(_r, err: SdlcError):
        body = {"code": err.code, "message": err.message}
        if err.code in ("TOKENS_EXHAUSTED", "TASK_TOKEN_CEILING"):
            body["details"] = err.details
        return JSONResponse(status_code=err.http_status, content={"error": body})

    class Authz:
        async def assert_project_access(self, pid, user):
            return None

    class Db:
        async def get_project(self, pid):
            return {"id": pid}

    app.state.container = Container(settings=SimpleNamespace(JWT_SECRET=SECRET), token_gate=TokenGate(repo, mode="enforce", task_ceiling=0), authz=Authz(), db=Db())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", cookies={SESSION_COOKIE: sign_session(ANN, SECRET, 1)})
    return c, repo, ran


async def test_work_starts_when_nothing_is_limited_or_there_is_a_balance(client_and_repo):
    c, repo, ran = client_and_repo
    assert (await c.post("/api/projects/p1/work")).json() == {"ran": True, "admitted": True}
    repo.pools["project"] = pool(5)
    assert (await c.post("/api/projects/p1/work")).status_code == 200 and ran == ["p1", "p1"]


async def test_new_work_is_refused_with_the_reason_and_what_the_person_can_do(client_and_repo):
    c, repo, ran = client_and_repo
    repo.pools["project"] = pool(-12_000)
    r = await c.post("/api/projects/p1/work")
    assert r.status_code == 402 and ran == []                                    # the route body never ran
    e = r.json()["error"]
    assert e["code"] == "TOKENS_EXHAUSTED" and "used all of its tokens" in e["message"] and "-12k" in e["message"]
    assert e["details"] == {"pool": "project", "balance": -12_000, "projectId": "p1", "canRequest": True}
    assert repo.blocks[0]["entry"] == "work" and repo.blocks[0]["user_id"] == "ann"


async def test_reading_is_never_gated(client_and_repo):
    c, repo, _ = client_and_repo
    repo.pools["project"] = pool(-1)
    repo.pools["org"] = pool(-1)
    assert (await c.get("/api/projects/p1/read")).json() == {"read": True}


async def test_design_time_work_is_charged_to_the_open_project_and_refused_when_it_is_empty(client_and_repo):
    c, repo, ran = client_and_repo
    repo.pools["project"] = pool(-1)
    r = await c.post("/api/design/test", headers={"x-project-id": "p9"})
    assert r.status_code == 402 and r.json()["error"]["details"]["projectId"] == "p9"
    repo.pools["project"] = pool(50)
    assert (await c.post("/api/design/test", headers={"x-project-id": "p9"})).status_code == 200 and ran == ["design:p9"]
    # with no project open, only the organisation's pool applies (organisation library work)
    repo.pools["project"] = None
    repo.pools["org"] = pool(-5)
    r2 = await c.post("/api/design/test")
    assert r2.status_code == 402 and r2.json()["error"]["details"] == {"pool": "organisation", "balance": -5, "projectId": None, "canRequest": False}


async def test_an_unreadable_balance_refuses_with_a_503_not_a_pass(client_and_repo):
    c, repo, ran = client_and_repo
    repo.fail = True
    r = await c.post("/api/projects/p1/work")
    assert r.status_code == 503 and r.json()["error"]["code"] == "TOKENS_UNAVAILABLE" and ran == []


async def test_a_task_admitted_for_one_project_is_checked_again_when_it_moves_to_another():
    uc.reset(user_id="ann", project_id="p1", admitted=True, task=uc.TaskBudget(10))
    uc.update(project_id="p1", stage=2)                               # the same project: still admitted
    assert uc.current().admitted is True
    uc.update(project_id="p2")                                        # another project: the admission was for p1's balance
    assert uc.current().admitted is False and uc.current().task is None
    uc.update(project_id="p3", admitted=True)                         # an explicit admission for the new project is respected
    assert uc.current().admitted is True
    with uc.scope(project_id="p4"):
        assert uc.current().admitted is False
    assert uc.current().project_id == "p3" and uc.current().admitted is True


async def test_a_chat_that_names_its_project_in_the_body_is_gated_on_that_project(client_and_repo):
    from app.api import chat_routes

    c, repo, ran = client_and_repo
    app = c._transport.app                                            # type: ignore[attr-defined]
    app.include_router(chat_routes.router)
    app.state.container.chat = SimpleNamespace(handle=lambda **kw: ran.append("handled"))
    repo.pools["project"] = pool(-1)
    r = await c.post("/api/chat", json={"projectId": "p7", "message": "hello"})
    assert r.status_code == 402 and r.json()["error"]["details"]["projectId"] == "p7" and ran == []
