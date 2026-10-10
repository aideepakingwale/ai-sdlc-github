"""Token governance, phase 0: every provider attempt becomes one ledger row, attributed to a person,
a project and an activity (docs/token-governance.md)."""

from __future__ import annotations

import ast
import asyncio
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.integrations.llm import LlmClient
from app.services import usage_context as uc
from app.services.usage_ledger import UsageLedger, build_rows

APP = Path(__file__).resolve().parents[1] / "app"


@pytest.fixture(autouse=True)
def _clean_ctx():
    uc.reset()
    yield
    uc.reset()


def _attempt(provider="bedrock", outcome="ok", **usage: int) -> dict[str, Any]:
    u = {"promptTokens": 0, "completionTokens": 0, "inputTokens": 0, "cacheReadTokens": 0, "cacheWriteTokens": 0, **usage}
    return {"provider": provider, "model": "m", "outcome": outcome, "usage": u, "providerRequestId": f"{provider}-{outcome}",
            "stopReason": "end_turn", "region": "eu-west-1", "latencyMs": 12}


# ----------------------------------------------------------------------------- rows
def test_a_row_keeps_the_cache_split_and_the_total_is_everything_billed():
    uc.reset(user_id="u1", project_id="p1", stage=3, run_id="job-1", actor_type="job")
    rows = build_rows(request_id="r1", activity="stage.generate", tag="t",
                      usage_log=[_attempt(promptTokens=5210, inputTokens=10, cacheReadTokens=5000, cacheWriteTokens=200, completionTokens=300)])
    r = rows[0]
    assert (r["input_tokens"], r["cache_read_tokens"], r["cache_write_tokens"], r["output_tokens"]) == (10, 5000, 200, 300)
    assert (r["user_id"], r["project_id"], r["stage"], r["run_id"], r["actor_type"]) == ("u1", "p1", 3, "job-1", "job")
    assert (r["activity"], r["provider_request_id"], r["region"], r["billable"]) == ("stage.generate", "bedrock-ok", "eu-west-1", True)


def test_a_gateway_without_a_split_counts_the_whole_prompt_as_uncached_input():
    rows = build_rows(request_id="r", activity="other", tag=None, usage_log=[{"provider": "groq", "model": "m", "outcome": "ok",
                      "usage": {"promptTokens": 40, "completionTokens": 8}}])
    assert (rows[0]["input_tokens"], rows[0]["cache_read_tokens"], rows[0]["output_tokens"]) == (40, 0, 8)


def test_every_attempt_is_a_row_in_order_and_failures_keep_their_tokens():
    rows = build_rows(request_id="r", activity="stage.generate", tag="t", truncated=True, usage_log=[
        _attempt(outcome="refused", promptTokens=40, inputTokens=40, completionTokens=5),
        _attempt(outcome="canceled", promptTokens=0),
        _attempt(provider="gemini", promptTokens=9, inputTokens=9, completionTokens=3),
    ])
    assert [(r["attempt_no"], r["provider"], r["status"]) for r in rows] == [(1, "bedrock", "refused"), (2, "bedrock", "canceled"), (3, "gemini", "truncated")]
    assert rows[0]["input_tokens"] + rows[0]["output_tokens"] == 45


def test_mock_and_local_are_recorded_but_never_billable():
    rows = build_rows(request_id="r", activity="other", tag=None, usage_log=[_attempt(provider="mock", promptTokens=5, inputTokens=5), _attempt(provider="local"), _attempt(provider="bedrock")])
    assert [r["billable"] for r in rows] == [False, False, True]


def test_an_unknown_activity_is_recorded_as_other_and_the_context_can_override():
    rows = build_rows(request_id="r", activity="not.a.thing", tag=None, usage_log=[_attempt()])
    assert rows[0]["activity"] == "other"
    with uc.scope(activity="agent.stage_run"):
        assert build_rows(request_id="r", activity="agent.run", tag=None, usage_log=[_attempt()])[0]["activity"] == "agent.stage_run"
    assert build_rows(request_id="r", activity="agent.run", tag=None, usage_log=[_attempt()])[0]["activity"] == "agent.run"


# ----------------------------------------------------------------------------- context
async def test_the_context_follows_spawned_tasks_and_a_scope_restores_it():
    uc.reset(user_id="u1", project_id="p1")
    seen: list[Any] = []

    async def child() -> None:
        seen.append(uc.current().user_id)

    await asyncio.get_running_loop().create_task(child())
    with uc.scope(project_id="p2"):
        assert uc.current().project_id == "p2"
    assert seen == ["u1"] and uc.current().project_id == "p1"


async def test_set_run_context_feeds_the_ledger_but_keeps_the_user():
    from app.services.telemetry import set_run_context

    uc.reset(user_id="u1")
    set_run_context("p9", 4)
    c = uc.current()
    assert (c.user_id, c.project_id, c.stage) == ("u1", "p9", 4)


# ----------------------------------------------------------------------------- client
class _Rows:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def insert_usage_events(self, rows: list[dict[str, Any]]) -> None:
        self.rows += rows


def _client(monkeypatch, response: httpx.Response, sent: list[dict]) -> tuple[LlmClient, _Rows]:
    c = LlmClient("http://ai-client:8081")
    sink = _Rows()
    c.ledger = UsageLedger(sink)

    async def fake_post(url, json=None, **kw):
        sent.append(json)
        return httpx.Response(response.status_code, json=response.json(), request=httpx.Request("POST", url))

    monkeypatch.setattr(c._http, "post", fake_post)
    return c, sink


async def test_a_successful_call_writes_one_row_per_attempt_and_sends_the_request_id(monkeypatch):
    uc.reset(user_id="u1", project_id="p1")
    sent: list[dict] = []
    ok = httpx.Response(200, json={
        "provider": "bedrock", "model": "m", "content": "hi", "tier": "auto",
        "usage": {"promptTokens": 130, "completionTokens": 7, "inputTokens": 10, "cacheReadTokens": 100, "cacheWriteTokens": 20},
        "usageLog": [_attempt(outcome="error", promptTokens=3, inputTokens=3), _attempt(promptTokens=130, inputTokens=10, cacheReadTokens=100, cacheWriteTokens=20, completionTokens=7)],
    })
    c, sink = _client(monkeypatch, ok, sent)
    res = await c.generate(intent="standard", messages=[{"role": "user", "content": "x"}], tag="unit", activity="stage.generate")
    assert sent[0]["requestId"] == res.request_id and len(res.request_id) == 32
    assert [r["status"] for r in sink.rows] == ["error", "ok"] and {r["request_id"] for r in sink.rows} == {res.request_id}
    assert sum(r["cache_read_tokens"] for r in sink.rows) == 100 and sink.rows[1]["user_id"] == "u1"
    assert res.usage["promptTokens"] == 130 and res.usage["cacheReadTokens"] == 100


async def test_a_failed_call_still_records_what_was_billed(monkeypatch):
    uc.reset(user_id="u1", project_id="p1")
    fail = httpx.Response(502, json={"error": {"code": "PROVIDER_EXHAUSTED", "message": "all failed",
                                               "details": {"usageLog": [_attempt(outcome="refused", promptTokens=40, inputTokens=40, completionTokens=5)]}}})
    fail.headers["content-type"] = "application/json"
    c, sink = _client(monkeypatch, fail, [])

    async def post(url, json=None, **kw):
        return httpx.Response(502, json=fail.json(), headers={"content-type": "application/json"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(c._http, "post", post)
    from app.domain.errors import SdlcError

    with pytest.raises(SdlcError):
        await c.generate(intent="standard", messages=[{"role": "user", "content": "x"}], activity="stage.generate")
    assert [(r["status"], r["input_tokens"], r["output_tokens"]) for r in sink.rows] == [("refused", 40, 5)]


async def test_an_older_gateway_without_a_usage_log_still_produces_a_row(monkeypatch):
    uc.reset(project_id="p1")
    ok = httpx.Response(200, json={"provider": "gemini", "model": "g", "content": "x", "usage": {"promptTokens": 12, "completionTokens": 4}})
    c, sink = _client(monkeypatch, ok, [])
    await c.generate(intent="standard", messages=[{"role": "user", "content": "x"}], activity="other")
    assert [(r["provider"], r["input_tokens"], r["output_tokens"]) for r in sink.rows] == [("gemini", 12, 4)]


async def test_a_ledger_that_cannot_write_never_fails_the_generation(monkeypatch, caplog):
    class Broken:
        async def insert_usage_events(self, rows):
            raise RuntimeError("db down")

    ok = httpx.Response(200, json={"provider": "bedrock", "model": "m", "content": "x", "usage": {"promptTokens": 1, "completionTokens": 1}})
    c, _ = _client(monkeypatch, ok, [])
    c.ledger = UsageLedger(Broken())
    res = await c.generate(intent="standard", messages=[{"role": "user", "content": "x"}], activity="other")
    assert res.content == "x" and "usage ledger write FAILED" in caplog.text


async def test_the_stream_path_records_success_and_a_billed_failure(monkeypatch):
    uc.reset(user_id="u1", project_id="p1")
    c = LlmClient("http://ai-client:8081")
    sink = _Rows()
    c.ledger = UsageLedger(sink)
    events = {
        "ok": ['data: {"type":"delta","text":"he"}', 'data: {"type":"done","provider":"bedrock","model":"m","content":"he","usage":{"promptTokens":50,"completionTokens":2,"inputTokens":50},"tier":"auto","usageLog":[]}'],
        "err": ['data: {"type":"delta","text":"he"}', 'data: {"type":"error","message":"boom","usageLog":[' + __import__("json").dumps(_attempt(outcome="error", promptTokens=60, inputTokens=60, completionTokens=9, estimated=True)) + ']}'],
    }
    mode = {"v": "ok"}

    class Resp:
        status_code = 200

        async def aiter_lines(self):
            for line in events[mode["v"]]:
                yield line

    class Ctx:
        async def __aenter__(self):
            return Resp()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(c._http, "stream", lambda *a, **k: Ctx())
    res = await c.generate_stream(intent="generation", messages=[], on_delta=lambda t: None, activity="stage.generate")
    assert res.usage["promptTokens"] == 50
    assert [(r["status"], r["input_tokens"], r["output_tokens"]) for r in sink.rows] == [("ok", 50, 2)]   # no log from an older gateway -> synthesised
    sink.rows.clear()
    mode["v"] = "err"
    from app.domain.errors import SdlcError

    with pytest.raises(SdlcError):
        await c.generate_stream(intent="generation", messages=[], on_delta=lambda t: None, activity="stage.generate")
    assert [(r["status"], r["input_tokens"], r["output_tokens"], r["estimated"]) for r in sink.rows] == [("error", 60, 9, True)]


# ----------------------------------------------------------------------------- call-site audit
_METHODS = {"generate", "generate_json", "generate_stream"}


def _llm_calls() -> list[tuple[str, int, ast.Call]]:
    out = []
    for path in sorted(APP.rglob("*.py")):
        if path.name == "llm.py" and path.parent.name == "integrations":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in _METHODS:
                recv = ast.unparse(n.func.value)
                if "llm" in recv.lower():
                    out.append((str(path.relative_to(APP)), n.lineno, n))
    return out


def test_every_model_call_names_its_activity():
    calls = _llm_calls()
    assert len(calls) >= 30, "the audit found suspiciously few call sites - did the receiver naming change?"
    missing = [f"{f}:{ln}" for f, ln, c in calls if not any(k.arg == "activity" for k in c.keywords)]
    assert not missing, f"LLM calls without an activity (docs/token-governance.md): {missing}"


def test_every_literal_activity_is_in_the_taxonomy():
    bad = []
    for f, ln, c in _llm_calls():
        for k in c.keywords:
            if k.arg == "activity" and isinstance(k.value, ast.Constant) and k.value.value not in uc.ACTIVITIES:
                bad.append(f"{f}:{ln} {k.value.value!r}")
    assert not bad, bad


# ----------------------------------------------------------------------------- real Postgres
ADMIN = os.environ.get("TEST_DATABASE_URL")
needs_pg = pytest.mark.skipif(not ADMIN, reason="TEST_DATABASE_URL not set")


@needs_pg
async def test_the_ledger_table_stores_derives_totals_and_refuses_edits():
    import asyncpg

    from app.repos.pg import Database

    name = f"led_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(ADMIN)
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    db = Database(f"{ADMIN.rpartition('/')[0]}/{name}")
    await db.connect()
    try:
        await db.run_migrations(APP.parents[2] / "infra" / "migrations")
        uc.reset(user_id="u1", project_id="p1", stage=2)
        rows = build_rows(request_id="r1", activity="stage.generate", tag="t",
                          usage_log=[_attempt(promptTokens=5210, inputTokens=10, cacheReadTokens=5000, cacheWriteTokens=200, completionTokens=300)])
        await UsageLedger(db).record(request_id="r1", activity="stage.generate", tag="t",
                                     usage_log=[_attempt(promptTokens=5210, inputTokens=10, cacheReadTokens=5000, cacheWriteTokens=200, completionTokens=300)])
        got = await db.pool.fetchrow("SELECT * FROM llm_usage_events WHERE request_id='r1'")
        assert got["total_tokens"] == 5510 and got["project_id"] == "p1" and got["user_id"] == "u1" and got["billable"] is True
        assert rows[0]["input_tokens"] == got["input_tokens"]
        with pytest.raises(asyncpg.PostgresError, match="append-only"):
            await db.pool.execute("UPDATE llm_usage_events SET output_tokens=0")
        with pytest.raises(asyncpg.PostgresError, match="append-only"):
            await db.pool.execute("DELETE FROM llm_usage_events")
        async with db.pool.acquire() as conn, conn.transaction():                       # a deliberate maintenance session may prune
            await conn.execute("SET LOCAL app.ledger_maintenance = 'on'")
            await conn.execute("DELETE FROM llm_usage_events WHERE request_id='r1'")
        assert await db.pool.fetchval("SELECT count(*) FROM llm_usage_events") == 0
    finally:
        await db.close()
        admin = await asyncpg.connect(ADMIN)
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


# ----------------------------------------------------------------------------- the open project
async def test_a_project_header_is_attributed_only_for_a_project_the_person_can_use():
    from app.api.deps import _PROJECT_OK, _header_project
    from app.domain.errors import SdlcError

    class Authz:
        async def assert_project_access(self, pid, user):
            if pid == "denied":
                raise SdlcError("FORBIDDEN", "no")

    class Db:
        async def get_project(self, pid):
            return None if pid == "ghost" else {"id": pid}

    c = SimpleNamespace(authz=Authz(), db=Db())
    user = SimpleNamespace(id="u1")
    _PROJECT_OK.clear()

    def req(pid):
        return SimpleNamespace(headers={"x-project-id": pid} if pid else {})

    assert await _header_project(req("p1"), c, user) == "p1"
    assert await _header_project(req("denied"), c, user) is None          # not a member: never charged
    assert await _header_project(req("ghost"), c, user) is None           # an administrator naming a project that does not exist
    assert await _header_project(req(None), c, user) is None
    assert await _header_project(req("x" * 100), c, user) is None
