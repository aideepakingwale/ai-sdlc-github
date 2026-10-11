"""The token gate (phase 4): admission at zero or below, fail-closed, the activity in flight finishes, the per-task ceiling."""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.domain.errors import SdlcError
from app.domain.models import UserPublic
from app.integrations.llm import LlmClient
from app.services import usage_context as uc

ADMIN = os.environ.get("TEST_DATABASE_URL")
needs_pg = pytest.mark.skipif(not ADMIN, reason="TEST_DATABASE_URL not set")
MIG = Path(__file__).resolve().parents[3] / "infra" / "migrations"
ROOT = UserPublic(id="root", email="root@t", displayName="ROOT", role="SUPER_ADMIN")  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _ctx():
    uc.reset()
    yield
    uc.reset()


@pytest.fixture
async def world():
    import asyncpg

    from app.repos.pg import Database
    from app.repos.pool_repo import PoolRepo
    from app.services.authz import AuthzService
    from app.services.token_gate import TokenGate
    from app.services.token_pools import TokenPoolService
    from app.services.usage_ledger import UsageLedger

    name = f"gate_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(ADMIN)
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    db = Database(f"{ADMIN.rpartition('/')[0]}/{name}")
    await db.connect()
    await db.run_migrations(MIG)
    await db.pool.execute("INSERT INTO users (id,email,display_name,role,password_hash) VALUES ('root','root@t','ROOT','SUPER_ADMIN','x'),('ann','ann@t','ANN','DEV','x')")
    p1 = await db.create_project(name="Alpha", created_by="root")
    p2 = await db.create_project(name="Beta", created_by="root")
    repo = PoolRepo(db)
    pools = TokenPoolService(repo, AuthzService(db), db)
    ledger = UsageLedger(db, pools)

    def make_gate(mode="enforce", ceiling=0):
        return TokenGate(repo, mode=mode, task_ceiling=ceiling)

    try:
        yield SimpleNamespace(db=db, repo=repo, pools=pools, ledger=ledger, make_gate=make_gate, p1=p1["id"], p2=p2["id"])
    finally:
        await db.close()
        admin = await asyncpg.connect(ADMIN)
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def spend(w, project, tokens):
    uc.reset(user_id="ann", project_id=project, admitted=True)
    await w.ledger.record(request_id=uuid.uuid4().hex, activity="stage.generate", tag="t",
                          usage_log=[{"provider": "bedrock", "model": "m", "outcome": "ok", "usage": {"promptTokens": tokens, "inputTokens": tokens, "completionTokens": 0}}])


@needs_pg
async def test_work_is_never_blocked_by_a_pool_that_was_never_allocated(world):
    w = world
    await w.make_gate().admit(project_id=w.p1, user_id="ann", entry="plan")
    assert uc.current().admitted is True and uc.current().task is None


@needs_pg
async def test_a_balance_above_zero_admits_and_zero_or_below_refuses_with_a_clear_message(world):
    w = world
    gate = w.make_gate()
    await w.pools.allocate(ROOT, "project", w.p1, 1000)
    await gate.admit(project_id=w.p1, user_id="ann", entry="plan")                       # 1000 left
    await spend(w, w.p1, 1000)                                                           # exactly zero
    uc.reset(user_id="ann", project_id=w.p1)
    with pytest.raises(SdlcError) as e:
        await gate.admit(project_id=w.p1, user_id="ann", entry="plan")
    assert e.value.code == "TOKENS_EXHAUSTED" and e.value.http_status == 402
    assert e.value.details == {"pool": "project", "balance": 0, "projectId": w.p1, "canRequest": True}
    assert "used all of its tokens" in str(e.value) and "ask for more tokens" in str(e.value)
    assert uc.current().admitted is False                                                # a refused task is not admitted
    other = w.p2
    await gate.admit(project_id=other, user_id="ann", entry="plan")                      # another project is unaffected
    blocks = await w.db.pool.fetch("SELECT project_id, pool, balance, entry, enforced FROM token_gate_blocks")
    assert [dict(b) for b in blocks] == [{"project_id": w.p1, "pool": "project", "balance": 0, "entry": "plan", "enforced": True}]


@needs_pg
async def test_the_activity_in_flight_finishes_and_may_overdraw_but_the_next_task_is_refused(world):
    w = world
    gate = w.make_gate()
    await w.pools.allocate(ROOT, "project", w.p1, 500)
    uc.reset(user_id="ann", project_id=w.p1)
    await gate.admit(project_id=w.p1, user_id="ann", entry="trigger")
    await spend_in_task(w, 800)                                                          # takes the balance to -300
    uc.update(admitted=True)
    await gate.before_call("stage.generate")                                             # a later call of the same task still goes ahead
    assert (await w.repo.get(w.p1))["balance_tokens"] == -300
    uc.reset(user_id="ann", project_id=w.p1)                                             # a new task
    with pytest.raises(SdlcError) as e:
        await gate.before_call("stage.generate")
    assert e.value.code == "TOKENS_EXHAUSTED" and "-300" in str(e.value)


async def spend_in_task(w, tokens):
    ctx = uc.current()
    await w.ledger.record(request_id=uuid.uuid4().hex, activity="stage.generate", tag="t",
                          usage_log=[{"provider": "bedrock", "model": "m", "outcome": "ok", "usage": {"promptTokens": tokens, "inputTokens": tokens, "completionTokens": 0}}])
    assert uc.current().project_id == ctx.project_id


@needs_pg
async def test_an_empty_organisation_blocks_every_project_and_work_with_no_project(world):
    w = world
    gate = w.make_gate()
    await w.pools.allocate(ROOT, "org", "org", 100)
    await w.pools.allocate(ROOT, "project", w.p1, 1_000_000)
    await spend(w, w.p2, 150)                                                            # the organisation is now at -50
    for project in (w.p1, w.p2, None):
        uc.reset(user_id="ann", project_id=project)
        with pytest.raises(SdlcError) as e:
            await gate.admit(project_id=project, user_id="ann", entry="plan")
        assert e.value.details["pool"] == "organisation" and "The organisation has used all" in str(e.value)
    assert e.value.details["canRequest"] is False                                        # no project to ask on behalf of
    await w.pools.allocate(ROOT, "org", "org", 10_000)                                   # topped up: work starts again
    uc.reset(user_id="ann", project_id=w.p1)
    await gate.admit(project_id=w.p1, user_id="ann", entry="plan")


@needs_pg
async def test_if_the_balance_cannot_be_read_nothing_starts(world):
    w = world
    gate = w.make_gate()

    async def broken(_):
        raise RuntimeError("database down")

    w.repo.gate_state = broken
    with pytest.raises(SdlcError) as e:
        await gate.admit(project_id=w.p1, user_id="ann", entry="plan")
    assert e.value.code == "TOKENS_UNAVAILABLE" and e.value.http_status == 503
    assert uc.current().admitted is False


@needs_pg
async def test_observe_mode_records_what_would_be_refused_and_lets_it_through_and_off_checks_nothing(world):
    w = world
    await w.pools.allocate(ROOT, "project", w.p1, 10)
    await spend(w, w.p1, 50)
    uc.reset(user_id="ann", project_id=w.p1)
    await w.make_gate("observe").admit(project_id=w.p1, user_id="ann", entry="plan")
    assert uc.current().admitted is True
    assert [b["enforced"] for b in await w.db.pool.fetch("SELECT enforced FROM token_gate_blocks")] == [False]
    assert await w.repo.blocks_by_project() == {}                                         # only enforced blocks count
    uc.reset(user_id="ann", project_id=w.p1)
    await w.make_gate("off").admit(project_id=w.p1, user_id="ann", entry="plan")
    assert await w.db.pool.fetchval("SELECT count(*) FROM token_gate_blocks") == 1


@needs_pg
async def test_one_task_has_a_safety_ceiling_shared_by_everything_it_spawns(world):
    w = world
    gate = w.make_gate(ceiling=1000)
    await w.pools.allocate(ROOT, "project", w.p1, 1_000_000)
    uc.reset(user_id="ann", project_id=w.p1)
    await gate.admit(project_id=w.p1, user_id="ann", entry="trigger")
    assert uc.current().task.ceiling == 1000

    async def child(n):
        await gate.before_call("stage.generate")
        gate.note_spend([{"input_tokens": n, "cache_read_tokens": 0, "cache_write_tokens": 0, "output_tokens": 0, "billable": True}])

    await asyncio.gather(*[asyncio.get_running_loop().create_task(child(300)) for _ in range(3)])   # 900 spent in three parallel calls
    gate.note_spend([{"input_tokens": 400, "cache_read_tokens": 0, "cache_write_tokens": 0, "output_tokens": 0, "billable": True}, {"input_tokens": 999, "cache_read_tokens": 0, "cache_write_tokens": 0, "output_tokens": 0, "billable": False}])
    with pytest.raises(SdlcError) as e:
        await gate.before_call("stage.generate")
    assert e.value.code == "TASK_TOKEN_CEILING" and e.value.details == {"ceiling": 1000, "spent": 1300}
    # a pool nobody allocated to has no ceiling to protect
    uc.reset(user_id="ann", project_id=w.p2)
    await gate.admit(project_id=w.p2, user_id="ann", entry="trigger")
    assert uc.current().task is None


@needs_pg
async def test_the_model_client_refuses_before_calling_the_gateway_and_counts_what_a_task_uses(world, monkeypatch):
    w = world
    gate = w.make_gate(ceiling=100)
    client = LlmClient("http://ai-client:8081")
    client.ledger, client.gate = w.ledger, gate
    sent: list[dict] = []

    async def post(url, json=None, **kw):
        sent.append(json)
        body = {"provider": "bedrock", "model": "m", "content": "ok", "usage": {"promptTokens": 90, "completionTokens": 20, "inputTokens": 90},
                "usageLog": [{"provider": "bedrock", "model": "m", "outcome": "ok", "usage": {"promptTokens": 90, "completionTokens": 20, "inputTokens": 90}}]}
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(client._http, "post", post)
    await w.pools.allocate(ROOT, "project", w.p1, 100_000)
    uc.reset(user_id="ann", project_id=w.p1)
    call = lambda: client.generate(intent="standard", messages=[{"role": "user", "content": "x"}], activity="stage.generate")  # noqa: E731
    await call()                                                                          # un-admitted entry: admitted on its first call; 110 spent
    with pytest.raises(SdlcError) as e:
        await call()                                                                      # over the 100-token ceiling: stops at the next call
    assert e.value.code == "TASK_TOKEN_CEILING" and len(sent) == 1
    await spend(w, w.p1, 200_000)                                                         # now the pool is empty
    uc.reset(user_id="ann", project_id=w.p1)
    with pytest.raises(SdlcError) as e2:
        await call()
    assert e2.value.code == "TOKENS_EXHAUSTED" and len(sent) == 1                         # refused without reaching the gateway


@needs_pg
async def test_tasks_started_together_at_a_positive_balance_are_all_admitted_and_the_next_is_refused(world):
    w = world
    gate = w.make_gate()
    await w.pools.allocate(ROOT, "project", w.p1, 100)

    barrier = asyncio.Barrier(5)

    async def task():
        uc.reset(user_id="ann", project_id=w.p1)
        await gate.admit(project_id=w.p1, user_id="ann", entry="trigger")             # every task starts while 100 is left
        await barrier.wait()
        await spend_in_task(w, 400)

    await asyncio.gather(*[task() for _ in range(5)])                                     # overshoot is bounded by what is in flight
    assert (await w.repo.get(w.p1))["balance_tokens"] == 100 - 2000
    uc.reset(user_id="ann", project_id=w.p1)
    with pytest.raises(SdlcError):
        await gate.admit(project_id=w.p1, user_id="ann", entry="trigger")


# ----------------------------------------------------------------------------- the mode is a runtime setting
async def test_the_mode_comes_from_the_administrators_setting_with_the_environment_as_the_default():
    from app.services import token_gate as tg
    from app.services.token_gate import TokenGate

    value: dict = {"v": None}
    reads = {"n": 0, "fail": False}

    async def reader():
        reads["n"] += 1
        if reads["fail"]:
            raise RuntimeError("db down")
        return value["v"]

    gate = TokenGate(SimpleNamespace(), mode="observe", mode_reader=reader)
    assert await gate.mode() == "observe" and gate.default_mode == "observe"             # nothing set: the default
    value["v"] = "enforce"
    assert await gate.mode() == "observe" and reads["n"] == 1                            # cached for a few seconds: not a database read per call
    gate.forget_mode()
    assert await gate.mode() == "enforce"
    value["v"] = "nonsense"
    gate.forget_mode()
    assert await gate.mode() == "observe"                                                # an unknown value is ignored
    value["v"] = "enforce"
    gate.forget_mode()
    assert await gate.mode() == "enforce"
    reads["fail"] = True
    gate._cached = ("enforce", gate._cached[1] - tg._MODE_TTL - 1)                       # expired
    assert await gate.mode() == "enforce"                                                # a failed read keeps the last known mode: never looser
    assert await TokenGate(SimpleNamespace(), mode="bogus").mode() == "enforce"          # a bad default is the strict one


@needs_pg
async def test_an_administrator_moves_the_gate_from_observe_to_enforce_and_sees_what_it_would_have_done(world):
    w = world
    gate = w.make_gate("observe")
    gate._reader = lambda: w.db.get_setting("token_gate_mode")
    await w.pools.allocate(ROOT, "project", w.p1, 100)
    await spend(w, w.p1, 500)
    for _ in range(3):
        uc.reset(user_id="ann", project_id=w.p1)
        await gate.admit(project_id=w.p1, user_id="ann", entry="plan")                   # observe: let through, but remember
    st = await w.pools.gate_status(ROOT, gate)
    assert (st["mode"], st["default"], st["refused7d"], st["wouldRefuse7d"]) == ("observe", "observe", 0, 3)
    out = await w.pools.set_gate_mode(ROOT, gate, "enforce")
    assert out["mode"] == "enforce"
    uc.reset(user_id="ann", project_id=w.p1)
    with pytest.raises(SdlcError):
        await gate.admit(project_id=w.p1, user_id="ann", entry="plan")                   # the very next start is refused, no restart
    assert (await w.pools.gate_status(ROOT, gate))["refused7d"] == 1
    with pytest.raises(SdlcError):
        await w.pools.set_gate_mode(ROOT, gate, "sometimes")
    ann = UserPublic(id="ann", email="ann@t", displayName="ANN", role="DEV")  # type: ignore[arg-type]
    with pytest.raises(SdlcError):
        await w.pools.set_gate_mode(ann, gate, "off")                                    # administrators only
