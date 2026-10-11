"""Token pools (phase 3): allocation, consumption in the ledger transaction, negative balances, alerts, monthly top-ups."""

from __future__ import annotations

import asyncio
import os
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domain.errors import SdlcError
from app.domain.models import UserPublic
from app.repos.pool_repo import next_month_start
from app.services import usage_context as uc
from app.services.token_pools import forecast, level_for, state_name

ADMIN = os.environ.get("TEST_DATABASE_URL")
needs_pg = pytest.mark.skipif(not ADMIN, reason="TEST_DATABASE_URL not set")
MIG = Path(__file__).resolve().parents[3] / "infra" / "migrations"


def user(id_: str, role: str) -> UserPublic:
    return UserPublic(id=id_, email=f"{id_}@t", displayName=id_.upper(), role=role)  # type: ignore[arg-type]


ROOT, PM, ANN, EVE = user("root", "SUPER_ADMIN"), user("pm", "PROJECT_MANAGER"), user("ann", "DEV"), user("eve", "DEV")


# ----------------------------------------------------------------------------- pure
def test_the_alert_levels_follow_the_share_of_the_latest_allocation_that_is_left():
    assert [level_for(b, 1000) for b in (1000, 201, 200, 51, 50, 1, 0, -5)] == [0, 0, 1, 1, 2, 2, 3, 3]
    assert level_for(5, 0) == 0 and level_for(0, 0) == 3                       # no reference: only "empty" is known
    assert [state_name(b, level_for(b, 100)) for b in (100, 15, 3, 0, -4)] == ["ok", "low", "critical", "empty", "overdrawn"]


def test_the_forecast_needs_a_balance_and_a_burn():
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    assert forecast(1000, 100, now) == {"daysLeft": 10.0, "runsOutOn": "2026-10-20"}
    assert forecast(0, 100, now) == {"daysLeft": None, "runsOutOn": None}
    assert forecast(-5, 100, now)["daysLeft"] is None and forecast(500, 0, now)["daysLeft"] is None


def test_next_month_start_rolls_over_the_year():
    assert next_month_start(datetime(2026, 12, 31, 23, tzinfo=timezone.utc)) == datetime(2027, 1, 1, tzinfo=timezone.utc)
    assert next_month_start(datetime(2026, 10, 10, tzinfo=timezone.utc)) == datetime(2026, 11, 1, tzinfo=timezone.utc)


# ----------------------------------------------------------------------------- real Postgres
@pytest.fixture
async def world():
    import asyncpg

    from app.repos.pg import Database
    from app.repos.pool_repo import PoolRepo
    from app.services.authz import AuthzService
    from app.services.token_pools import TokenPoolService
    from app.services.usage_ledger import UsageLedger

    name = f"pool_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(ADMIN)
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    db = Database(f"{ADMIN.rpartition('/')[0]}/{name}")
    await db.connect()
    await db.run_migrations(MIG)
    for uid, role in (("root", "SUPER_ADMIN"), ("pm", "PROJECT_MANAGER"), ("ann", "DEV"), ("eve", "DEV")):
        await db.pool.execute("INSERT INTO users (id,email,display_name,role,password_hash) VALUES ($1,$2,$3,$4,'x')", uid, f"{uid}@t", uid.upper(), role)
    p1 = await db.create_project(name="Alpha", created_by="pm")
    p2 = await db.create_project(name="Beta", created_by="pm")
    await db.add_member(project_id=p1["id"], user_id="ann", role="DEV", added_by="pm")
    hooks: list[str] = []

    class Http:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None): hooks.append(json["text"])

    svc = TokenPoolService(PoolRepo(db), AuthzService(db), db, webhook_url="http://hook", http=Http())
    ledger = UsageLedger(db, svc)
    n = 0

    async def spend(project, tokens, *, provider="bedrock", out=0, user_id="ann"):
        """One ledger write through the real writer; `tokens` is the input side."""
        nonlocal n
        n += 1
        uc.reset(user_id=user_id, project_id=project)
        await ledger.record(request_id=f"r{n}", activity="stage.generate", tag="t", usage_log=[
            {"provider": provider, "model": "m", "outcome": "ok", "usage": {"promptTokens": tokens, "inputTokens": tokens, "completionTokens": out}}])

    try:
        yield SimpleNamespace(db=db, svc=svc, p1=p1["id"], p2=p2["id"], spend=spend, hooks=hooks, ledger=ledger)
    finally:
        uc.reset()
        await db.close()
        admin = await asyncpg.connect(ADMIN)
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def pool(w, scope="project", sid=None):
    return await w.svc._repo.get("org" if scope == "org" else (sid or w.p1))


@needs_pg
async def test_an_unallocated_pool_is_unlimited_and_nothing_is_counted_against_it(world):
    w = world
    await w.spend(w.p1, 5000)
    assert await pool(w) is None and (await w.svc.project(ANN, w.p1))["metered"] is False
    assert (await w.svc.project(ANN, w.p1))["state"] == "unmetered" and (await w.svc.project(ANN, w.p1))["balance"] is None


@needs_pg
async def test_once_allocated_billable_tokens_count_and_offline_ones_do_not(world):
    w = world
    await w.svc.allocate(ROOT, "project", w.p1, 10_000)
    await w.spend(w.p1, 3000, out=500)
    await w.spend(w.p1, 9999, provider="mock")                                    # offline: recorded in the ledger, never counted
    await w.spend(w.p2, 700)                                                      # another project: not this pool
    p = await w.svc.project(ANN, w.p1)
    assert (p["allocated"], p["consumed"], p["balance"], p["state"]) == (10_000, 3500, 6500, "ok")
    assert (await pool(w, sid=w.p2)) is None


@needs_pg
async def test_the_organisation_pool_counts_everything_billable_including_calls_with_no_project(world):
    w = world
    await w.svc.allocate(ROOT, "org", "org", 100_000)
    await w.spend(w.p1, 1000)
    await w.spend(w.p2, 2000)
    await w.spend(None, 300)
    org = await pool(w, "org")
    assert org["consumed_tokens"] == 3300 and org["balance_tokens"] == 96_700
    assert (await w.svc.project(ANN, w.p1))["organisation"] == {"metered": True, "balance": 96_700, "state": "ok", "level": 0}


@needs_pg
async def test_the_balance_may_go_negative_and_the_empty_notice_is_sent_exactly_once(world):
    w = world
    await w.svc.allocate(ROOT, "project", w.p1, 1000)
    await asyncio.gather(*[w.spend(w.p1, 300) for _ in range(6)])                 # 1800 against 1000, written concurrently
    p = await pool(w)
    assert p["consumed_tokens"] == 1800 and p["balance_tokens"] == -800 and p["alert_level"] == 3
    notes = await w.db.pool.fetch("SELECT kind, title, body FROM notifications WHERE project_id=$1 AND kind='token_balance' ORDER BY created_at", w.p1)
    titles = [n["title"] for n in notes]
    assert titles.count("This project has used up its tokens") == 1                # not once per writer
    assert (await w.svc.project(ANN, w.p1))["state"] == "overdrawn"
    assert any("used up its tokens" in h for h in w.hooks)


@needs_pg
async def test_low_then_critical_are_announced_as_the_balance_falls_and_a_top_up_starts_over(world):
    w = world
    await w.svc.allocate(ROOT, "project", w.p1, 10_000)
    await w.spend(w.p1, 7900)                                                     # 21% left: fine
    assert (await pool(w))["alert_level"] == 0
    await w.spend(w.p1, 200)                                                      # 19% left: low
    assert (await pool(w))["alert_level"] == 1
    await w.spend(w.p1, 1400)                                                     # 5% left: critical
    assert (await pool(w))["alert_level"] == 2
    await w.spend(w.p1, 100)                                                      # still critical: nothing new
    titles = [r["title"] for r in await w.db.pool.fetch("SELECT title FROM notifications WHERE kind='token_balance' ORDER BY created_at")]
    assert titles == ["This project is low on tokens", "This project is critically low on tokens"]
    await w.svc.allocate(ROOT, "project", w.p1, 50_000, note="more")
    assert (await pool(w))["alert_level"] == 0                                    # topped up: warnings start over
    await w.spend(w.p1, 48_000)
    assert (await pool(w))["alert_level"] == 2


@needs_pg
async def test_counting_can_start_at_the_beginning_of_the_month(world):
    w = world
    await w.spend(w.p1, 4000)                                                     # spent before any allocation
    await w.svc.allocate(ROOT, "project", w.p1, 10_000, count_from="month_start")
    first = await pool(w)
    assert first["consumed_tokens"] == 4000 or datetime.now(timezone.utc).day == 1
    w2 = await w.svc.allocate(ROOT, "project", w.p2, 10_000)                       # the default counts from now
    assert (await pool(w, sid=w.p2))["consumed_tokens"] == 0 and w2["pool"]["metered"]


@needs_pg
async def test_consumption_always_equals_the_ledger_even_under_concurrent_writers(world):
    w = world
    await w.svc.allocate(ROOT, "project", w.p1, 1_000_000)
    await w.svc.allocate(ROOT, "org", "org", 5_000_000)
    rnd = random.Random(7)
    await asyncio.gather(*[w.spend(rnd.choice([w.p1, w.p2, None]), rnd.randint(1, 900), out=rnd.randint(0, 50), provider=rnd.choice(["bedrock", "bedrock", "mock"])) for _ in range(60)])
    for scope, sid in (("project", w.p1), ("org", "org")):
        p = await pool(w, scope, sid)
        assert p["consumed_tokens"] == await w.svc._repo.ledger_since(p["id"], p["metered_since"])
        r = await w.svc.recompute(ROOT, scope, sid)
        assert r["changed"] is False
    await w.db.pool.execute("UPDATE token_pools SET consumed_tokens = consumed_tokens + 999 WHERE id=$1", w.p1)    # a manual slip
    fixed = await w.svc.recompute(ROOT, "project", w.p1)
    assert fixed["changed"] is True and fixed["after"] == fixed["before"] - 999


@needs_pg
async def test_allocation_is_for_administrators_and_is_validated(world):
    w = world
    for bad in (ANN, PM):
        with pytest.raises(SdlcError) as e:
            await w.svc.allocate(bad, "project", w.p1, 100)
        assert e.value.code == "FORBIDDEN"
    for args in ((w.p1, 0), (w.p1, 10**13), (w.p1, -5)):                             # zero, absurd, and a take-back with no reason
        with pytest.raises(SdlcError):
            await w.svc.allocate(ROOT, "project", *args)
    with pytest.raises(SdlcError):
        await w.svc.allocate(ROOT, "project", "no-such", 100)
    with pytest.raises(SdlcError):
        await w.svc.allocate(ROOT, "project", w.p1, -100, kind="monthly", note="x")
    await w.svc.allocate(ROOT, "project", w.p1, 1000)
    await w.svc.allocate(ROOT, "project", w.p1, -300, note="given to Beta")
    assert (await pool(w))["allocated_tokens"] == 700
    d = await w.svc.detail(ROOT, "project", w.p1)
    assert [(g["tokens"], g["note"]) for g in d["grants"]] == [(-300, "given to Beta"), (1000, "")] and d["grants"][0]["by"] == "ROOT"


@needs_pg
async def test_members_see_their_balance_and_strangers_do_not(world):
    w = world
    await w.svc.allocate(ROOT, "project", w.p1, 1000)
    assert (await w.svc.project(ANN, w.p1))["balance"] == 1000
    with pytest.raises(SdlcError):
        await w.svc.project(EVE, w.p1)
    with pytest.raises(SdlcError):
        await w.svc.overview(ANN)


@needs_pg
async def test_monthly_allocations_top_up_once_each_month_and_can_be_stopped(world):
    w = world
    now = datetime(2026, 10, 10, 9, tzinfo=timezone.utc)
    await w.svc.allocate(ROOT, "project", w.p1, 100_000, kind="monthly", note="plan", now=now)
    p = await pool(w)
    assert p["allocated_tokens"] == 100_000                                       # this month's allocation is given now
    sched = (await w.svc.detail(ROOT, "project", w.p1))["schedules"]
    assert len(sched) == 1 and sched[0]["nextRunAt"].startswith("2026-11-01")
    assert await w.svc.apply_due_schedules(now + timedelta(days=5)) == 0          # not due yet
    nov = datetime(2026, 11, 1, 0, 5, tzinfo=timezone.utc)
    runs = await asyncio.gather(*[w.svc.apply_due_schedules(nov) for _ in range(3)])
    assert sum(runs) == 1                                                         # three replicas, one top-up
    assert (await pool(w))["allocated_tokens"] == 200_000
    assert (await w.svc.detail(ROOT, "project", w.p1))["schedules"][0]["nextRunAt"].startswith("2026-12-01")
    kinds = [g["kind"] for g in (await w.svc.detail(ROOT, "project", w.p1))["grants"]]
    assert kinds == ["monthly", "monthly"]
    await w.svc.stop_schedule(ROOT, sched[0]["id"])
    assert await w.svc.apply_due_schedules(datetime(2026, 12, 2, tzinfo=timezone.utc)) == 0
    with pytest.raises(SdlcError):
        await w.svc.stop_schedule(ROOT, sched[0]["id"])
    assert (await w.svc.project(ANN, w.p1))["monthly"] is None


@needs_pg
async def test_the_overview_shows_what_is_carved_from_the_organisation_and_warns_when_it_is_over_subscribed(world):
    w = world
    o = await w.svc.allocate(ROOT, "org", "org", 1000)
    assert "warning" not in o
    await w.svc.allocate(ROOT, "project", w.p1, 600)
    r = await w.svc.allocate(ROOT, "project", w.p2, 600)
    assert "more than the organisation" in r["warning"]
    ov = await w.svc.overview(ROOT)
    assert (ov["allocatedToProjects"], ov["unallocated"], ov["overAllocated"]) == (1200, -200, True)
    assert {p["name"]: p["metered"] for p in ov["projects"]} == {"Alpha": True, "Beta": True}
    assert ov["organisation"]["metered"] is True


@needs_pg
async def test_a_failing_webhook_or_notification_never_breaks_the_ledger_write(world):
    w = world
    w.svc._webhook = "http://unreachable.invalid"
    w.svc._http = None
    await w.svc.allocate(ROOT, "project", w.p1, 100)
    await w.spend(w.p1, 500)                                                      # empties the pool; the webhook cannot be reached
    assert (await pool(w))["consumed_tokens"] == 500
    assert await w.db.pool.fetchval("SELECT count(*) FROM llm_usage_events WHERE project_id=$1", w.p1) == 1


@needs_pg
async def test_deleting_a_project_removes_its_pool_but_keeps_the_ledger(world):
    w = world
    await w.svc.allocate(ROOT, "project", w.p2, 1000, kind="monthly")
    await w.spend(w.p2, 100)
    assert await w.db.delete_project(w.p2) is True
    assert await w.db.pool.fetchval("SELECT count(*) FROM token_pools WHERE id=$1", w.p2) == 0
    assert await w.db.pool.fetchval("SELECT count(*) FROM token_schedules") == 0 and await w.db.pool.fetchval("SELECT count(*) FROM token_grants") == 0
    assert await w.db.pool.fetchval("SELECT count(*) FROM llm_usage_events WHERE project_id=$1", w.p2) == 1


@needs_pg
async def test_asking_for_more_tokens_tells_the_project_once_and_is_cleared_by_an_allocation(world):
    w = world
    await w.svc.allocate(ROOT, "project", w.p1, 100)
    await w.spend(w.p1, 500)
    r1 = await w.svc.request_tokens(ANN, w.p1, "Need it for the release")
    r2 = await w.svc.request_tokens(ANN, w.p1, "again")                              # one open request per person
    assert (r1["already"], r2["already"]) == (False, True)
    note = await w.db.pool.fetchrow("SELECT title, body FROM notifications WHERE kind='token_balance' AND title LIKE '%asked%'")
    assert note["title"] == "Alpha asked for more tokens" and "Need it for the release" in note["body"]
    assert any("asked for more tokens" in h for h in w.hooks)
    with pytest.raises(SdlcError):
        await w.svc.request_tokens(EVE, w.p1, "not a member")
    with pytest.raises(SdlcError):
        await w.svc.requests(ANN)                                                      # administrators only
    open_ = await w.svc.requests(ROOT)
    assert [(r["project"], r["by"], r["note"], r["balance"]) for r in open_] == [("Alpha", "ANN", "Need it for the release", -400)]
    await w.svc.allocate(ROOT, "project", w.p1, 100)                                   # still negative: the request stays open
    assert len(await w.svc.requests(ROOT)) == 1
    await w.svc.allocate(ROOT, "project", w.p1, 10_000)                                # balance above zero: whoever asked now has tokens
    assert await w.svc.requests(ROOT) == []
    assert await w.db.pool.fetchval("SELECT status FROM token_requests") == "granted"
    again = await w.svc.request_tokens(ANN, w.p1)                                      # a new request is possible afterwards
    assert again["already"] is False
    await w.svc.dismiss_request(ROOT, (await w.svc.requests(ROOT))[0]["id"])
    assert await w.svc.requests(ROOT) == []
    with pytest.raises(SdlcError):
        await w.svc.dismiss_request(ROOT, "nope")


@needs_pg
async def test_the_overview_counts_refused_starts_per_project_for_the_week(world):
    w = world
    await w.db.pool.execute("INSERT INTO token_gate_blocks (project_id, user_id, pool, balance, entry) VALUES ($1,'ann','project',-5,'plan'),($1,'ann','project',-5,'plan'),($2,'ann','project',-5,'plan')", w.p1, w.p2)
    await w.db.pool.execute("INSERT INTO token_gate_blocks (project_id, user_id, pool, balance, entry, ts) VALUES ($1,'ann','project',-5,'plan', now() - interval '10 days')", w.p1)
    await w.db.pool.execute("INSERT INTO token_gate_blocks (project_id, user_id, pool, balance, entry, enforced) VALUES ($1,'ann','project',-5,'plan', FALSE)", w.p1)
    ov = await w.svc.overview(ROOT)
    assert {p["name"]: p["blocked7d"] for p in ov["projects"]} == {"Alpha": 2, "Beta": 1}


@needs_pg
async def test_the_metrics_summarise_balances_and_the_gate_without_per_project_series(world):
    from app.services.token_gate import TokenGate

    w = world
    await w.svc.allocate(ROOT, "org", "org", 1000)
    await w.svc.allocate(ROOT, "project", w.p1, 1000)
    await w.svc.allocate(ROOT, "project", w.p2, 1000)
    await w.spend(w.p1, 850)                                                          # Alpha: 15% left, low
    await w.spend(w.p2, 1500)                                                         # Beta: overdrawn
    await w.db.pool.execute("INSERT INTO token_gate_blocks (project_id, pool, balance, entry, enforced) VALUES ($1,'project',-500,'plan',TRUE),($1,'project',-500,'plan',FALSE)", w.p2)
    m = await w.svc.metrics(TokenGate(w.svc._repo, mode="observe"))
    assert m["orgBalance"] == 1000 - 2350 and m["projectStates"] == {"low": 1, "overdrawn": 1}
    assert (m["refused7d"], m["wouldRefuse7d"], m["mode"]) == (1, 1, "observe")
