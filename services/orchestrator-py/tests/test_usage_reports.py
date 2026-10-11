"""Token usage reports (phase 1): the numbers, and who is allowed to see which."""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.domain.errors import SdlcError
from app.domain.models import UserPublic
from app.services.usage_reports import CSV_ROW_CAP, _csv_cell, parse_range

ADMIN = os.environ.get("TEST_DATABASE_URL")
needs_pg = pytest.mark.skipif(not ADMIN, reason="TEST_DATABASE_URL not set")
MIG = Path(__file__).resolve().parents[3] / "infra" / "migrations"


# ----------------------------------------------------------------------------- pure helpers
def test_the_range_defaults_to_the_last_thirty_days_and_rejects_nonsense():
    s, e = parse_range(None, None, today=date(2026, 10, 31))
    assert (s, e) == (date(2026, 10, 2), date(2026, 10, 31))
    assert parse_range("2026-01-01", "2026-01-01")[0] == date(2026, 1, 1)
    for bad in (("2026-02-01", "2026-01-01"), ("2024-01-01", "2026-01-01"), ("yesterday", None)):
        with pytest.raises(SdlcError):
            parse_range(*bad)


def test_a_spreadsheet_cannot_run_a_cell_as_a_formula():
    assert _csv_cell("=HYPERLINK(\"x\")") == "'=HYPERLINK(\"x\")" and _csv_cell("+1") == "'+1" and _csv_cell("-2") == "'-2"
    assert _csv_cell("@x") == "'@x" and _csv_cell("plain") == "plain" and _csv_cell(5) == 5
    assert CSV_ROW_CAP >= 5000


# ----------------------------------------------------------------------------- real Postgres
def _u(id_: str, role: str) -> UserPublic:
    return UserPublic(id=id_, email=f"{id_}@t", displayName=id_.upper(), role=role)  # type: ignore[arg-type]


@pytest.fixture
async def world():
    import asyncpg

    from app.repos.pg import Database
    from app.repos.usage_repo import UsageRepo
    from app.services.authz import AuthzService
    from app.services.usage_reports import UsageReports

    name = f"rep_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(ADMIN)
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    db = Database(f"{ADMIN.rpartition('/')[0]}/{name}")
    await db.connect()
    await db.run_migrations(MIG)
    for uid, role in (("root", "SUPER_ADMIN"), ("pm", "PROJECT_MANAGER"), ("ann", "DEV"), ("bob", "DEV"), ("eve", "DEV")):
        await db.pool.execute("INSERT INTO users (id,email,display_name,role,password_hash) VALUES ($1,$2,$3,$4,'x')", uid, f"{uid}@t", uid.upper(), role)
    p1 = await db.create_project(name="Alpha", created_by="pm")
    p2 = await db.create_project(name="Beta", created_by="pm")
    for uid in ("ann", "bob"):
        await db.add_member(project_id=p1["id"], user_id=uid, role="DEV", added_by="pm")
    now = datetime.now(timezone.utc)
    n = 0

    async def put(project, user, activity, tokens_in=100, out=10, cache=0, status="ok", provider="bedrock", days_ago=0, agent=None, stage=1, est=False):
        nonlocal n
        n += 1
        await db.pool.execute(
            """INSERT INTO llm_usage_events (id, request_id, attempt_no, provider, model, project_id, stage, user_id, activity, agent_id,
               input_tokens, cache_read_tokens, output_tokens, status, billable, estimated, ts)
               VALUES ($1,$2,1,$3,'m',$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)""",
            f"e{n}", f"r{n}", provider, project, stage, user, activity, agent, tokens_in, cache, out, status,
            provider not in ("mock", "local"), est, now - timedelta(days=days_ago))

    await put(p1["id"], "ann", "stage.generate", 1000, 200, cache=800)               # 2000
    await put(p1["id"], "ann", "stage.generate", 500, 100, status="refused")         # 600 (waste)
    await put(p1["id"], "bob", "stage.validate", 300, 50, days_ago=2)                # 350
    await put(p1["id"], "bob", "agent.run", 200, 30, agent="a1", provider="mock")    # 230, offline
    await put(p2["id"], "eve", "stage.generate", 4000, 1000)                         # 5000 on Beta
    await put(None, None, "other", 50, 5)                                            # 55, unattributed
    svc = UsageReports(UsageRepo(db), AuthzService(db))
    try:
        yield svc, p1["id"], p2["id"], db
    finally:
        await db.close()
        admin = await asyncpg.connect(ADMIN)
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


@needs_pg
async def test_a_manager_sees_every_person_and_the_totals_add_up(world):
    svc, p1, _, _ = world
    r = await svc.project(_u("pm", "PROJECT_MANAGER"), p1, None, None)
    assert r["viewer"]["canSeeEveryone"] is True
    assert r["totals"]["tokens"] == 2000 + 600 + 350                                # billable only: the mock call is left out
    assert r["totals"]["calls"] == 3 and r["totals"]["cacheReadTokens"] == 800
    assert r["totals"]["cacheReadShare"] == round(800 / (1000 + 800 + 500 + 300), 4)
    assert {p["name"]: p["tokens"] for p in r["people"]} == {"ANN": 2600, "BOB": 350}
    assert r["byActivity"][0]["activity"] == "stage.generate" and r["byActivity"][0]["tokens"] == 2600
    assert r["totals"]["wasteTokens"] == 600 and r["waste"] == [{"activity": "stage.generate", "status": "refused", "calls": 1, "tokens": 600}]
    assert sum(d["tokens"] for d in r["byDay"]) == r["totals"]["tokens"] and len(r["byDay"]) == 2


@needs_pg
async def test_a_member_sees_the_project_total_and_only_their_own_line(world):
    svc, p1, _, _ = world
    r = await svc.project(_u("bob", "DEV"), p1, None, None)
    assert r["viewer"]["canSeeEveryone"] is False
    assert r["totals"]["tokens"] == 2950                                              # the project total
    assert [(p["name"], p["tokens"], p["you"]) for p in r["people"]] == [("BOB", 350, True)]
    assert r["you"]["tokens"] == 350
    mine = await svc.project(_u("bob", "DEV"), p1, None, None, person="me")
    assert mine["totals"]["tokens"] == 350 and mine["projectTotal"]["tokens"] == 2950
    with pytest.raises(SdlcError) as e:
        await svc.project(_u("bob", "DEV"), p1, None, None, person="ann")
    assert e.value.code == "FORBIDDEN"
    ev = await svc.events(_u("bob", "DEV"), p1, None, None, "billable", None, None, None, 100, 0)
    assert {x["userId"] for x in ev["events"]} == {"bob"}                             # a member's drill-down is their own rows


@needs_pg
async def test_a_manager_can_look_at_one_person_and_a_stranger_sees_nothing(world):
    svc, p1, p2, _ = world
    one = await svc.project(_u("pm", "PROJECT_MANAGER"), p1, None, None, person="ann")
    assert one["totals"]["tokens"] == 2600 and one["projectTotal"]["tokens"] == 2950
    with pytest.raises(SdlcError):
        await svc.project(_u("eve", "DEV"), p1, None, None)                     # not a member of Alpha
    other_pm = _u("pm2", "PROJECT_MANAGER")
    assert (await svc.project(_u("root", "SUPER_ADMIN"), p2, None, None))["totals"]["tokens"] == 5000
    assert (await svc.project(_u("pm", "PROJECT_MANAGER"), p2, None, None))["viewer"]["canSeeEveryone"] is True
    _ = other_pm


@needs_pg
async def test_offline_tokens_are_a_separate_view_and_dates_narrow_the_range(world):
    svc, p1, _, _ = world
    off = await svc.project(_u("pm", "PROJECT_MANAGER"), p1, None, None, billable="offline")
    assert off["totals"]["tokens"] == 230
    allv = await svc.project(_u("pm", "PROJECT_MANAGER"), p1, None, None, billable="all")
    assert allv["totals"]["tokens"] == 3180
    today = datetime.now(timezone.utc).date().isoformat()
    t = await svc.project(_u("pm", "PROJECT_MANAGER"), p1, today, today)
    assert t["totals"]["tokens"] == 2600                                              # Bob's two-days-ago call is outside the range
    agents = await svc.project(_u("pm", "PROJECT_MANAGER"), p1, None, None, billable="all")
    assert agents["byAgent"][0]["tokens"] == 230


@needs_pg
async def test_the_organisation_view_is_for_administrators_and_stops_at_projects(world):
    svc, p1, p2, _ = world
    with pytest.raises(SdlcError):
        await svc.organisation(_u("pm", "PROJECT_MANAGER"), None, None)
    o = await svc.organisation(_u("root", "SUPER_ADMIN"), None, None)
    assert {p["name"]: p["tokens"] for p in o["byProject"]} == {"Beta": 5000, "Alpha": 2950}
    assert o["totals"]["tokens"] == 2950 + 5000 + 55
    assert o["unattributed"] == {"calls": 1, "tokens": 55}
    assert [p["deleted"] for p in o["byProject"]] == [False, False]
    assert "people" in o["byProject"][0] and "userId" not in str(o["byProject"])
    csv_text = await svc.organisation_csv(_u("root", "SUPER_ADMIN"), None, None, "billable")
    assert csv_text.splitlines()[0].startswith("project,tokens") and "Beta,5000" in csv_text


@needs_pg
async def test_the_export_and_drilldown_carry_the_ledger_rows(world):
    svc, p1, _, _ = world
    text = await svc.project_csv(_u("pm", "PROJECT_MANAGER"), p1, None, None, "billable", None)
    lines = text.strip().splitlines()
    assert lines[0].startswith("ts,person,activity") and len(lines) == 1 + 3
    mine = await svc.project_csv(_u("ann", "DEV"), p1, None, None, "billable", None)
    assert len(mine.strip().splitlines()) == 1 + 2 and "BOB" not in mine
    ev = await svc.events(_u("pm", "PROJECT_MANAGER"), p1, None, None, "billable", None, "stage.generate", "refused", 50, 0)
    assert [(e["status"], e["totalTokens"]) for e in ev["events"]] == [("refused", 600)]
