"""Regression tests for the independent review's findings (real Postgres)."""

import datetime as dt

import pytest

from app.agile.rules import assert_stage_mutable
from app.domain.errors import SdlcError

from .helpers import _approve, _finish_project_stages, _stage

pytestmark = pytest.mark.asyncio


async def _sprint(e, **kw):
    await e.agile.enable(e.pid, e.pm, methodology="scrum")
    await _finish_project_stages(e)
    return await e.agile.start_sprint(e.pid, e.po, **kw)


async def _st(e, who, key, target):
    cur = next(r for r in await e.pg.list_backlog(e.pid) if r["item_key"] == key)
    return await e.backlog.set_status(e.pid, who, key, target, expected_version=cur["version"])


async def _ready(e, title, est=3):
    v = await e.backlog.create(e.pid, e.po, {"title": title, "estimate": est, "acceptanceCriteria": ["ok"]})
    return await e.backlog.set_status(e.pid, e.po, v["key"], "ready", expected_version=v["version"])


async def test_reconcile_repairs_a_failed_hook_and_a_stuck_lifecycle(env):
    e = env
    s = await _sprint(e)
    await _approve(e, "refine@S-001")
    plan = await _stage(e, "plan@S-001")
    boom = {"on": True}

    async def flaky(_ctx):
        if boom["on"]:
            raise RuntimeError("transient")
    e.agile._on_approved.setdefault("plan", []).insert(0, flaky)
    await _approve(e, "plan@S-001")                                   # approval stands even though a hook failed
    assert "agile.hook_failed" in e.audit.events
    boom["on"] = False
    assert await e.agile.reconcile(e.pid) >= 1
    assert (await e.pg.get_open_iteration(e.pid))["status"] == "active"
    assert plan["seq"] and s["label"] == "S-001"
    assert await e.agile.reconcile(e.pid) >= 1                        # idempotent: a second pass changes nothing


async def test_hook_failure_does_not_block_sprint_close(env):
    e = env
    await _sprint(e)

    async def always_fails(_ctx):
        raise RuntimeError("nope")
    e.agile._on_approved.setdefault("retro", []).append(always_fails)
    for k in ("refine", "plan"):
        await _approve(e, f"{k}@S-001")
    await _approve(e, "build@S-001", by=e.admin)
    await _approve(e, "review@S-001")
    await _approve(e, "retro@S-001")
    assert (await e.pg.list_iterations(e.pid))[0]["status"] == "closed"


async def test_closed_sprint_is_read_only_history(env):
    e = env
    await _sprint(e)
    item = await _ready(e, "Checkout")
    await e.backlog.add_to_sprint(e.pid, e.po, item["key"])
    for k in ("refine", "plan"):
        await _approve(e, f"{k}@S-001")
    await _st(e, e.dev, item["key"], "in_progress")
    await _st(e, e.po, item["key"], "done")
    await _approve(e, "build@S-001", by=e.admin)
    await _approve(e, "review@S-001")
    await _approve(e, "retro@S-001")
    with pytest.raises(SdlcError) as err:                                  # a done item cannot be quietly reopened
        await _st(e, e.po, item["key"], "ready")
    assert err.value.code == "GATE_CONFLICT" and "closed" in err.value.message
    stage = await _stage(e, "build@S-001")
    with pytest.raises(SdlcError) as err:
        await assert_stage_mutable(e.pg, stage)
    assert err.value.code == "GATE_CONFLICT"
    await assert_stage_mutable(e.pg, await _stage(e, "vision"))             # project stages stay editable


async def test_cancel_refuses_once_items_are_started_and_detaches_the_rest(env):
    e = env
    s = await _sprint(e)
    a, b = await _ready(e, "A"), await _ready(e, "B")
    await e.backlog.add_to_sprint(e.pid, e.po, a["key"])
    await e.backlog.add_to_sprint(e.pid, e.po, b["key"])
    await _st(e, e.dev, a["key"], "in_progress")
    with pytest.raises(SdlcError) as err:
        await e.agile.cancel_sprint(e.pid, e.pm, s["id"])
    assert "started or finished" in err.value.message
    await _st(e, e.po, a["key"], "in_sprint")        # board "Back" must work
    await e.agile.cancel_sprint(e.pid, e.pm, s["id"])
    rows = {r["item_key"]: r for r in await e.pg.list_backlog(e.pid)}
    assert rows[a["key"]]["status"] == "ready" and rows[a["key"]]["iteration_id"] is None
    assert rows[b["key"]]["status"] == "ready" and rows[b["key"]]["iteration_id"] is None


async def test_start_sprint_rejects_out_of_range_dates(env):
    e = env
    await e.agile.enable(e.pid, e.pm, methodology="scrum")
    await _finish_project_stages(e)
    for bad in (dt.date(9999, 12, 31), dt.date(1999, 1, 1)):
        with pytest.raises(SdlcError) as err:
            await e.agile.start_sprint(e.pid, e.po, starts_on=bad)
        assert err.value.code == "VALIDATION_FAILED"


async def test_second_hardening_is_a_conflict_not_a_404(env):
    e = env
    await _sprint(e)
    for k in ("refine", "plan"):
        await _approve(e, f"{k}@S-001")
    await _approve(e, "build@S-001", by=e.admin)
    await _approve(e, "review@S-001")
    await _approve(e, "retro@S-001")
    await e.agile.start_release_hardening(e.pid, e.po)
    with pytest.raises(SdlcError) as err:
        await e.agile.start_release_hardening(e.pid, e.po)
    assert err.value.code == "GATE_CONFLICT"


async def test_add_to_sprint_is_capacity_checked_atomically(env):
    e = env
    await _sprint(e, capacity=5)
    a, b = await _ready(e, "A", 3), await _ready(e, "B", 3)
    await e.backlog.add_to_sprint(e.pid, e.po, a["key"])
    with pytest.raises(SdlcError) as err:
        await e.backlog.add_to_sprint(e.pid, e.po, b["key"])
    assert "overcommit" in err.value.message
    res = await e.backlog.add_to_sprint(e.pid, e.po, b["key"], force=True)
    assert res["status"] == "in_sprint"
    assert any(r["detail"].get("overcommit") for r in e.audit.records if r["event"] == "sprint.item_added")
