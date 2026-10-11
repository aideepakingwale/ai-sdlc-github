"""Failed-call capture (docs/token-governance.md, section 20): what was asked and what came back, for calls that went wrong."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from pydantic import BaseModel

from app.domain.errors import SdlcError
from app.domain.models import UserPublic
from app.integrations.llm import LlmClient
from app.services import usage_context as uc
from app.services.failure_capture import FailureCapture, attempts_of, failure_reason

ADMIN = os.environ.get("TEST_DATABASE_URL")
needs_pg = pytest.mark.skipif(not ADMIN, reason="TEST_DATABASE_URL not set")
MIG = Path(__file__).resolve().parents[3] / "infra" / "migrations"


def attempt(outcome="ok", provider="bedrock", error=None, **usage: int) -> dict[str, Any]:
    u = {"promptTokens": 10, "completionTokens": 5, "inputTokens": 10, "cacheReadTokens": 0, "cacheWriteTokens": 0, **usage}
    a = {"provider": provider, "model": "m", "outcome": outcome, "usage": u, "providerRequestId": f"{provider}-{outcome}", "stopReason": "end_turn", "latencyMs": 7}
    if error:
        a["error"], a["errorKind"] = error, "transient"
    return a


class MemRepo:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def save(self, row: dict[str, Any]) -> None:
        self.rows[row["request_id"]] = row

    async def purge(self, days: int) -> int:
        return 0


def client(handler, *, capture: FailureCapture | None) -> tuple[LlmClient, MemRepo]:
    repo = MemRepo()
    c = LlmClient("http://ai-client:8081")
    c._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    c.captures = capture if capture is not None else FailureCapture(repo)
    if capture is None:
        return c, repo
    return c, capture._repo


def ok_body(content="done", log=None, truncated=False) -> dict[str, Any]:
    return {"provider": "bedrock", "model": "m", "content": content, "usage": {"promptTokens": 10, "completionTokens": 5}, "attempts": [],
            "tier": "auto", "truncated": truncated, "usageLog": log if log is not None else [attempt()]}


PROMPT = [{"role": "system", "content": "You are a careful analyst."}, {"role": "user", "content": "Summarise the Aurora payments spec."}]


async def call(c: LlmClient) -> Any:
    uc.reset(user_id="ann", project_id="p1", stage=2)
    return await c.generate(intent="generation", messages=PROMPT, activity="stage.generate", tag="t1")


# ----------------------------------------------------------------------------- pure
def test_a_clean_request_is_not_a_failure_and_the_reason_follows_the_last_attempt():
    assert failure_reason([attempt()]) is None
    assert failure_reason([attempt()], truncated=True) == "truncated"
    assert failure_reason([attempt("error", error="boom"), attempt()]) == "failed_over"
    assert failure_reason([attempt("error"), attempt("refused")]) == "refused"
    assert failure_reason([attempt("canceled")]) == "canceled"
    assert attempts_of([attempt("error", error="boom")])[0] == {"provider": "bedrock", "model": "m", "outcome": "error", "errorKind": "transient",
                                                              "error": "boom", "providerRequestId": "bedrock-error", "stopReason": "end_turn", "latencyMs": 7}


# ----------------------------------------------------------------------------- the client
async def test_a_refused_request_keeps_the_prompt_and_what_the_gateway_said():
    body = {"error": {"code": "PROVIDER_EXHAUSTED", "message": "Every provider failed", "details": {"usageLog": [attempt("refused", error="content policy")]}}}
    c, repo = client(lambda r: httpx.Response(502, json=body), capture=None)
    with pytest.raises(SdlcError):
        await call(c)
    (row,) = repo.rows.values()
    assert row["reason"] == "refused" and row["error"] == "Every provider failed"
    assert json.loads(row["request_body"]) == PROMPT                                    # exactly what was sent
    assert "Every provider failed" in row["response_body"]
    assert (row["project_id"], row["user_id"], row["activity"], row["stage"], row["tag"]) == ("p1", "ann", "stage.generate", 2, "t1")
    assert row["attempts"][0]["error"] == "content policy"


async def test_a_request_that_needed_another_provider_keeps_the_answer_and_the_first_error():
    log = [attempt("error", provider="groq", error="429 rate limited", promptTokens=0, completionTokens=0), attempt()]
    c, repo = client(lambda r: httpx.Response(200, json=ok_body("the answer", log)), capture=None)
    res = await call(c)
    row = repo.rows[res.request_id]
    assert row["reason"] == "failed_over" and row["response_body"] == "the answer" and row["error"] == "429 rate limited"
    assert [a["provider"] for a in row["attempts"]] == ["groq", "bedrock"]


async def test_an_answer_cut_off_at_the_limit_is_kept():
    c, repo = client(lambda r: httpx.Response(200, json=ok_body("{\"a\": 1", truncated=True)), capture=None)
    res = await call(c)
    assert repo.rows[res.request_id]["reason"] == "truncated" and repo.rows[res.request_id]["response_body"] == "{\"a\": 1"


async def test_a_clean_request_leaves_nothing_behind():
    c, repo = client(lambda r: httpx.Response(200, json=ok_body()), capture=None)
    await call(c)
    assert repo.rows == {}


async def test_an_unreachable_gateway_is_kept_too():
    def boom(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    c, repo = client(boom, capture=None)
    with pytest.raises(SdlcError):
        await call(c)
    (row,) = repo.rows.values()
    assert row["reason"] == "unreachable" and "connection refused" in row["error"] and json.loads(row["request_body"]) == PROMPT


class Shape(BaseModel):
    name: str


async def test_an_answer_the_application_rejects_keeps_both_sides():
    c, repo = client(lambda r: httpx.Response(200, json=ok_body("this is not json at all")), capture=None)
    uc.reset(user_id="ann", project_id="p1")
    with pytest.raises(SdlcError, match="schema-valid"):
        await c.generate_json(intent="generation", messages=PROMPT, schema=Shape, activity="stage.generate", max_attempts=2)
    assert len(repo.rows) == 2                                                          # one per attempt, each with its own request id
    first = next(iter(repo.rows.values()))
    assert first["reason"] == "invalid_output" and first["response_body"] == "this is not json at all" and first["error"].startswith("Output rejected")


async def test_images_are_left_out_and_long_text_is_cut_and_flagged():
    cap = FailureCapture(MemRepo(), max_chars=1_000)
    body = {"error": {"code": "X", "message": "nope", "details": {"usageLog": [attempt("error", error="bad")]}}}
    c, repo = client(lambda r: httpx.Response(502, json=body), capture=cap)
    uc.reset(user_id="ann", project_id="p1")
    big = [{"role": "user", "content": "x" * 5_000, "images": ["data:image/png;base64," + "A" * 4_000]}]
    with pytest.raises(SdlcError):
        await c.generate(intent="generation", messages=big, activity="stage.generate")
    (row,) = repo.rows.values()
    assert row["request_cut"] is True and len(row["request_body"]) == 1_000
    assert "AAAA" not in row["request_body"]


async def test_switched_off_means_nothing_is_stored_and_the_prompt_is_not_even_rendered():
    cap = FailureCapture(MemRepo(), enabled=False)
    body = {"error": {"code": "X", "message": "nope", "details": {"usageLog": [attempt("error")]}}}
    c, repo = client(lambda r: httpx.Response(502, json=body), capture=cap)
    with pytest.raises(SdlcError):
        await call(c)
    assert repo.rows == {} and cap.enabled is False and FailureCapture(MemRepo(), days=0).enabled is False


async def test_a_storage_error_never_breaks_the_call_being_diagnosed():
    class Broken(MemRepo):
        async def save(self, row: dict[str, Any]) -> None:
            raise RuntimeError("disk full")

    body = {"error": {"code": "X", "message": "gateway said no", "details": {}}}
    c, _ = client(lambda r: httpx.Response(502, json=body), capture=FailureCapture(Broken()))
    with pytest.raises(SdlcError, match="gateway said no"):                             # the original error, not the storage one
        await call(c)


# ----------------------------------------------------------------------------- the reports
@pytest.fixture
async def world():
    from app.repos.capture_repo import CaptureRepo
    from app.repos.pg import Database
    from app.repos.usage_repo import UsageRepo
    from app.services.authz import AuthzService
    from app.services.usage_reports import UsageReports

    if not ADMIN:
        pytest.skip("TEST_DATABASE_URL not set")
    name = f"cap_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(ADMIN)
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    db = Database(f"{ADMIN.rpartition('/')[0]}/{name}")
    await db.connect()
    await db.run_migrations(MIG)
    for uid, role in (("root", "SUPER_ADMIN"), ("pm", "PROJECT_MANAGER"), ("ann", "DEV"), ("eve", "DEV"), ("pm2", "PROJECT_MANAGER")):
        await db.pool.execute("INSERT INTO users (id,email,display_name,role,password_hash) VALUES ($1,$2,$3,$4,'x')", uid, f"{uid}@t", uid.upper(), role)
    p1 = await db.create_project(name="Alpha", created_by="pm")
    p2 = await db.create_project(name="Beta", created_by="pm2")
    await db.add_member(project_id=p1["id"], user_id="ann", role="DEV", added_by="pm")
    repo = CaptureRepo(db)
    svc = UsageReports(UsageRepo(db), AuthzService(db), repo, 14)
    n = 0

    async def ledger(project: str, request: str, attempts: list[str], days_ago: int = 0) -> None:
        nonlocal n
        for i, st in enumerate(attempts, 1):
            n += 1
            await db.pool.execute(
                """INSERT INTO llm_usage_events (id, request_id, attempt_no, provider, model, project_id, stage, user_id, activity, input_tokens, output_tokens, status, ts)
                   VALUES ($1,$2,$3,'bedrock','m',$4,2,'ann','stage.generate',100,20,$5,$6)""", f"e{n}", request, i, project, st, datetime.now(timezone.utc) - timedelta(days=days_ago))

    try:
        yield svc, repo, db, p1["id"], p2["id"], ledger
    finally:
        await db.close()
        admin = await asyncpg.connect(ADMIN)
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


def person(uid: str, role: str) -> UserPublic:
    return UserPublic(id=uid, email=f"{uid}@t", displayName=uid.upper(), role=role)


def row(request_id: str, project: str, **kw: Any) -> dict[str, Any]:
    return {"request_id": request_id, "project_id": project, "user_id": "ann", "activity": "stage.generate", "stage": 2, "tag": "t", "reason": "refused",
            "error": "Every provider failed", "attempts": [{"provider": "bedrock", "outcome": "error", "error": "429"}, {"provider": "bedrock", "outcome": "refused", "error": "policy"}],
            "request_body": json.dumps(PROMPT), "response_body": "I can't help with that.", **kw}


@needs_pg
async def test_a_manager_opens_a_failed_request_and_sees_prompt_response_and_each_attempt(world):
    svc, repo, _db, p1, _p2, ledger = world
    await ledger(p1, "r1", ["error", "refused"])
    await repo.save(row("r1", p1))
    d = await svc.request_detail(person("pm", "PROJECT_MANAGER"), p1, "r1")
    assert d["capture"]["messages"] == PROMPT and d["capture"]["response"] == "I can't help with that." and d["capture"]["reason"] == "refused"
    assert [(a["attempt"], a["status"], a["error"]) for a in d["attempts"]] == [(1, "error", "429"), (2, "refused", "policy")]
    assert d["totalTokens"] == 240 and d["activity"] == "stage.generate" and d["capture"]["expiresAt"]
    assert (await svc.request_detail(person("root", "SUPER_ADMIN"), p1, "r1"))["capture"]["reason"] == "refused"


@needs_pg
async def test_a_member_a_stranger_and_another_projects_manager_cannot_read_the_text(world):
    svc, repo, _db, p1, p2, ledger = world
    await ledger(p1, "r1", ["error"])
    await repo.save(row("r1", p1))
    for who in (person("ann", "DEV"), person("eve", "DEV"), person("pm2", "PROJECT_MANAGER")):
        with pytest.raises(SdlcError) as e:
            await svc.request_detail(who, p1, "r1")
        assert e.value.code == "FORBIDDEN"
    with pytest.raises(SdlcError) as e:                                                 # a request id from another project is simply not found
        await svc.request_detail(person("pm2", "PROJECT_MANAGER"), p2, "r1")
    assert e.value.code == "NOT_FOUND"


@needs_pg
async def test_the_calls_list_marks_what_can_be_opened_only_for_those_who_may_open_it(world):
    svc, repo, _db, p1, _p2, ledger = world
    await ledger(p1, "ok-but-rejected", ["ok"])
    await ledger(p1, "clean", ["ok"])
    await ledger(p1, "failed", ["error"])
    await repo.save(row("ok-but-rejected", p1, reason="invalid_output"))
    mgr = await svc.events(person("pm", "PROJECT_MANAGER"), p1, None, None, "billable", None, None, None, 50, 0)
    assert {e["requestId"]: e["hasCapture"] for e in mgr["events"]} == {"ok-but-rejected": True, "clean": False, "failed": False}
    failed = await svc.events(person("pm", "PROJECT_MANAGER"), p1, None, None, "billable", None, None, "failed", 50, 0)
    assert sorted(e["requestId"] for e in failed["events"]) == ["failed", "ok-but-rejected"]      # includes the call that "succeeded" but was rejected
    member = await svc.events(person("ann", "DEV"), p1, None, None, "billable", None, None, None, 50, 0)
    assert not any(e["hasCapture"] for e in member["events"])


@needs_pg
async def test_a_request_with_no_text_says_why_and_still_shows_its_attempts(world):
    svc, _repo, _db, p1, _p2, ledger = world
    await ledger(p1, "fresh", ["error"])
    await ledger(p1, "old", ["error"], days_ago=30)
    fresh = await svc.request_detail(person("pm", "PROJECT_MANAGER"), p1, "fresh")
    assert fresh["capture"] is None and "No text was kept" in fresh["captureNote"] and len(fresh["attempts"]) == 1
    assert "has been deleted" in (await svc.request_detail(person("pm", "PROJECT_MANAGER"), p1, "old"))["captureNote"]
    with pytest.raises(SdlcError) as e:
        await svc.request_detail(person("pm", "PROJECT_MANAGER"), p1, "nothing-here")
    assert e.value.code == "NOT_FOUND"


@needs_pg
async def test_a_cut_prompt_is_returned_as_text_and_a_second_failure_keeps_the_first_attempts(world):
    svc, repo, _db, p1, _p2, ledger = world
    await ledger(p1, "r1", ["error", "ok"])
    await repo.save(row("r1", p1, reason="failed_over", request_body='[{"role": "user", "content": "cut off mid', request_cut=True, response_body="answer"))
    await repo.save({"request_id": "r1", "project_id": p1, "reason": "invalid_output", "error": "Output rejected: x", "attempts": [], "response_body": "answer"})
    d = await svc.request_detail(person("pm", "PROJECT_MANAGER"), p1, "r1")
    assert d["capture"]["messages"] is None and d["capture"]["promptText"].startswith('[{"role"') and d["capture"]["promptCut"] is True
    assert d["capture"]["reason"] == "invalid_output" and d["attempts"][0]["error"] == "429"       # the earlier attempts were not wiped


@needs_pg
async def test_old_captures_are_deleted_and_so_are_a_deleted_projects(world):
    svc, repo, db, p1, p2, ledger = world
    await repo.save(row("old", p1))
    await db.pool.execute("UPDATE llm_failure_captures SET created_at = now() - interval '20 days' WHERE request_id='old'")
    await repo.save(row("new", p1))
    await repo.save(row("other", p2))
    assert await repo.purge(14) == 1
    assert await db.pool.fetchval("SELECT count(*) FROM llm_failure_captures") == 2
    await db.delete_project(p1)
    assert [r["request_id"] for r in await db.pool.fetch("SELECT request_id FROM llm_failure_captures")] == ["other"]


@needs_pg
async def test_a_request_that_never_reached_a_provider_is_still_listed_under_what_went_wrong(world):
    svc, repo, _db, p1, p2, ledger = world
    await repo.save(row("never-sent", p1, reason="unreachable", error="ai-client unreachable", attempts=[], response_body=None))
    await repo.save(row("elsewhere", p2, reason="unreachable"))                         # another project's: never shown here
    await ledger(p1, "billed", ["error"])
    mgr = person("pm", "PROJECT_MANAGER")
    failed = await svc.events(mgr, p1, None, None, "billable", None, None, "failed", 50, 0)
    got = {e["requestId"]: e for e in failed["events"]}
    assert set(got) == {"never-sent", "billed"}
    assert (got["never-sent"]["status"], got["never-sent"]["totalTokens"], got["never-sent"]["hasCapture"], got["never-sent"]["errorClass"]) == ("error", 0, True, "unreachable")
    assert (await svc.events(mgr, p1, None, None, "billable", None, "stage.generate", "failed", 50, 0))["events"][0]["requestId"] in got
    assert (await svc.events(mgr, p1, None, None, "billable", None, "agent.run", "failed", 50, 0))["events"] == []   # the activity filter applies to both halves
    assert [e["requestId"] for e in (await svc.events(mgr, p1, None, None, "billable", None, None, None, 50, 0))["events"]] == ["billed"]   # only the failed view adds them
    d = await svc.request_detail(mgr, p1, "never-sent")                                  # and it can be opened though the ledger has nothing
    assert d["capture"]["reason"] == "unreachable" and d["attempts"] == [] and d["totalTokens"] == 0
