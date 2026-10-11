"""Token pools: the balance a project, and the organisation above it, may spend (docs/token-governance.md, phase 3).

Phase 3 records and reports; it blocks nothing. The rules it records:
  * a pool belongs to a project, or to the organisation ("org"); an administrator allocates tokens to it;
  * the balance is allocated minus consumed and may go negative (the last activity may overdraw);
  * a pool that has never been allocated to is unlimited and not counted;
  * consumption is billable ledger tokens: a project's pool counts that project's, the organisation's counts everything.
Only administrators allocate; everyone on a project sees its balance.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from ..domain.errors import SdlcError
from ..domain.models import UserPublic
from ..repos.pool_repo import ORG, PoolRepo, next_month_start, pool_id

log = logging.getLogger("token-pools")

MAX_TOKENS = 1_000_000_000_000
LOW_SHARE, CRITICAL_SHARE = 0.20, 0.05
LEVELS = {0: "ok", 1: "low", 2: "critical", 3: "empty"}


def level_for(balance: int, reference: int) -> int:
    """0 fine, 1 low (20% of the latest allocation left), 2 critical (5%), 3 empty (zero or below)."""
    if balance <= 0:
        return 3
    if reference > 0:
        if balance <= reference * CRITICAL_SHARE:
            return 2
        if balance <= reference * LOW_SHARE:
            return 1
    return 0


def state_name(balance: int, level: int) -> str:
    return "overdrawn" if balance < 0 else LEVELS[level]


def forecast(balance: int, per_day: float, today: datetime | None = None) -> dict[str, Any]:
    """Days until the balance reaches zero at the recent daily burn, and the date. None when nothing is being spent or nothing is left."""
    if balance <= 0 or per_day <= 0:
        return {"daysLeft": None, "runsOutOn": None}
    days = balance / per_day
    when = (today or datetime.now(timezone.utc)) + timedelta(days=days)
    return {"daysLeft": round(days, 1), "runsOutOn": when.date().isoformat() if days < 3650 else None}


def _view(p: dict[str, Any] | None, per_day: float, monthly: int | None) -> dict[str, Any]:
    if not p or not p["metered"]:
        return {"metered": False, "allocated": 0, "consumed": 0, "balance": None, "referenceTokens": 0, "level": 0, "state": "unmetered", "burnPerDay": round(per_day), "monthly": None,
                "meteredSince": None, "daysLeft": None, "runsOutOn": None}
    level = level_for(p["balance_tokens"], p["reference_tokens"])
    return {"metered": True, "allocated": p["allocated_tokens"], "consumed": p["consumed_tokens"], "balance": p["balance_tokens"], "referenceTokens": p["reference_tokens"],
            "level": level, "state": state_name(p["balance_tokens"], level), "burnPerDay": round(per_day), "monthly": monthly,
            "meteredSince": p["metered_since"].isoformat() if p["metered_since"] else None, **forecast(p["balance_tokens"], per_day)}


def _fmt(n: int) -> str:
    a = abs(n)
    s = f"{a / 1_000_000:.1f}M" if a >= 1_000_000 else f"{round(a / 1000)}k" if a >= 10_000 else f"{a:,}"
    return f"-{s}" if n < 0 else s


class TokenPoolService:
    def __init__(self, repo: PoolRepo, authz: Any, db: Any, webhook_url: str = "", http: Any = None) -> None:
        self._repo, self._authz, self._db, self._webhook, self._http = repo, authz, db, webhook_url, http

    # ------------------------------------------------------------------ reading
    async def project(self, user: UserPublic, project_id: str) -> dict[str, Any]:
        """The balance everyone on a project can see (and, when it is the thing that would stop work, the organisation's)."""
        await self._authz.assert_project_access(project_id, user)
        name = await self._repo.project_name(project_id)
        if name is None:
            raise SdlcError("NOT_FOUND", "Project not found")
        v = _view(await self._repo.get(project_id), await self._repo.burn_per_day(project_id), await self._monthly(project_id))
        org = _view(await self._repo.get(ORG), await self._repo.burn_per_day(ORG), None)
        return {"projectId": project_id, "name": name, **v,
                "organisation": {"metered": org["metered"], "balance": org["balance"], "state": org["state"], "level": org["level"]}}

    async def _monthly(self, pid: str) -> int | None:
        s = await self._repo.schedules(pid)
        return sum(x["tokens"] for x in s) or None

    async def overview(self, user: UserPublic) -> dict[str, Any]:
        self._admin(user)
        org_row = await self._repo.get(ORG)
        org = {**_view(org_row, await self._repo.burn_per_day(ORG), await self._monthly(ORG))}
        projects = []
        for p in await self._repo.projects():
            pool = p if p["metered"] else None
            per_day = p["used_30d"] / 30
            v = _view({"metered": True, "allocated_tokens": p["allocated_tokens"], "consumed_tokens": p["consumed_tokens"], "balance_tokens": p["balance_tokens"],
                       "reference_tokens": p["reference_tokens"], "metered_since": p["metered_since"]} if pool else None, per_day, None)
            projects.append({"projectId": p["project_id"], "name": p["name"], "used30d": p["used_30d"], **v})
        carved = await self._repo.allocated_to_projects()
        return {"organisation": org, "projects": projects, "allocatedToProjects": carved,
                "unallocated": (org_row["allocated_tokens"] - carved) if org_row and org_row["metered"] else None,
                "overAllocated": bool(org_row and org_row["metered"] and carved > org_row["allocated_tokens"])}

    async def detail(self, user: UserPublic, scope_type: str, scope_id: str) -> dict[str, Any]:
        self._admin(user)
        pid = await self._scope(scope_type, scope_id)
        grants = await self._repo.grants(pid)
        return {"grants": [{"id": g["id"], "tokens": g["tokens"], "kind": g["kind"], "by": g["display_name"] or g["email"] or "", "note": g["note"] or "", "at": g["created_at"].isoformat()} for g in grants],
                "schedules": [{"id": s["id"], "tokens": s["tokens"], "nextRunAt": s["next_run_at"].isoformat(), "note": s["note"] or ""} for s in await self._repo.schedules(pid)]}

    # ------------------------------------------------------------------ changing
    async def allocate(self, user: UserPublic, scope_type: str, scope_id: str, tokens: int, kind: str = "one_off", note: str = "", count_from: str = "now",
                       now: datetime | None = None) -> dict[str, Any]:
        self._admin(user)
        pid = await self._scope(scope_type, scope_id)
        if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens == 0 or abs(tokens) > MAX_TOKENS:
            raise SdlcError("VALIDATION_FAILED", f"Allocate a whole number of tokens between 1 and {MAX_TOKENS:,}")
        if kind not in ("one_off", "monthly"):
            raise SdlcError("VALIDATION_FAILED", "kind must be one_off or monthly")
        if kind == "monthly" and tokens < 0:
            raise SdlcError("VALIDATION_FAILED", "A monthly allocation must be positive")
        if count_from not in ("now", "month_start"):
            raise SdlcError("VALIDATION_FAILED", "countFrom must be now or month_start")
        note = (note or "").strip()[:300]
        if tokens < 0 and not note:
            raise SdlcError("VALIDATION_FAILED", "Say why tokens are being taken back")
        now = now or datetime.now(timezone.utc)
        sid = None
        if kind == "monthly":
            sid = await self._repo.add_schedule(scope_type=scope_type, scope_id=scope_id, tokens=tokens, by=user.id, note=note or None, next_run_at=next_month_start(now))
        state = await self._repo.grant(scope_type=scope_type, scope_id=scope_id, tokens=tokens, kind=kind, by=user.id, note=note or None, count_from=count_from,
                                       level_for=level_for, schedule_id=sid, now=now)
        log.info("token allocation: %s %s %+d (%s) by %s", scope_type, scope_id, tokens, kind, user.email)
        out = {"pool": _view(state, await self._repo.burn_per_day(pid), await self._monthly(pid))}
        if scope_type == "project":
            org = await self._repo.get(ORG)
            carved = await self._repo.allocated_to_projects()
            if org and org["metered"] and carved > org["allocated_tokens"]:
                out["warning"] = f"Projects are now allocated {_fmt(carved)} in total, more than the organisation's {_fmt(org['allocated_tokens'])}."
        return out

    async def stop_schedule(self, user: UserPublic, sid: str) -> None:
        self._admin(user)
        if not await self._repo.stop_schedule(sid):
            raise SdlcError("NOT_FOUND", "No active monthly allocation with that id")

    async def recompute(self, user: UserPublic, scope_type: str, scope_id: str) -> dict[str, Any]:
        """Re-derive a pool's consumption from the ledger (repair after a manual correction or a restore)."""
        self._admin(user)
        pid = await self._scope(scope_type, scope_id)
        pool = await self._repo.get(pid)
        if not pool or not pool["metered"]:
            raise SdlcError("VALIDATION_FAILED", "That pool has no allocation yet, so nothing is counted against it")
        expected = await self._repo.ledger_since(pid, pool["metered_since"])
        after = await self._repo.set_consumed(pid, expected, level_for(pool["allocated_tokens"] - expected, pool["reference_tokens"]))
        return {"before": pool["consumed_tokens"], "after": after["consumed_tokens"], "changed": pool["consumed_tokens"] != after["consumed_tokens"]}

    async def apply_due_schedules(self, now: datetime | None = None) -> int:
        """Top up every pool whose monthly allocation is due (idempotent: a run is claimed by the statement that advances it)."""
        now = now or datetime.now(timezone.utc)
        n = 0
        for s in await self._repo.claim_due_schedules(now):
            scope = await self._repo.pool_scope(s["pool_id"])
            if not scope:
                continue
            await self._repo.grant(scope_type=scope[0], scope_id=scope[1], tokens=s["tokens"], kind="monthly", by=s["created_by"], note=s["note"] or "Monthly allocation",
                                   count_from="now", level_for=level_for, schedule_id=s["id"], now=now)
            n += 1
        return n

    # ------------------------------------------------------------------ alerts
    async def after_consumption(self, states: list[dict[str, Any]]) -> None:
        """Called after the ledger counted tokens: announce a pool that has just got low, critical or empty (once per level per top-up)."""
        for p in states:
            level = level_for(p["balance_tokens"], p["reference_tokens"])
            if level <= p["alert_level"] or level == 0:
                continue
            try:
                if await self._repo.claim_alert(p["id"], level):
                    await self._announce(p, level)
            except Exception:  # noqa: BLE001 — a notification failure must never disturb the ledger write
                log.warning("token balance alert failed for %s", p["id"], exc_info=True)

    async def _announce(self, p: dict[str, Any], level: int) -> None:
        bal, ref = p["balance_tokens"], p["reference_tokens"]
        who = "The organisation" if p["scope_type"] == "org" else "This project"
        if level == 3:
            title, body = f"{who} has used up its tokens", f"{_fmt(bal)} tokens left. An administrator can allocate more."
        else:
            share = round(100 * bal / ref) if ref else 0
            title = f"{who} is {'critically ' if level == 2 else ''}low on tokens"
            body = f"{_fmt(bal)} tokens left ({share}% of the latest allocation of {_fmt(ref)}). An administrator can allocate more."
        log.warning("TOKEN BALANCE %s: %s %s: %s", LEVELS[level].upper(), p["scope_type"], p["scope_id"], body)
        if p["scope_type"] == "project":
            await self._db.insert_notification(project_id=p["scope_id"], phase=None, kind="token_balance", title=title, body=body, roles=[])
        await self._post_webhook(f"{title}: {body}" + ("" if p["scope_type"] == "org" else f" (project {p['scope_id'][:8]})"))

    async def _post_webhook(self, text: str) -> None:
        if not self._webhook:
            return
        try:
            import httpx
            async with (self._http or httpx.AsyncClient(timeout=5.0)) as c:
                await c.post(self._webhook, json={"text": text})
        except Exception:  # noqa: BLE001
            log.warning("token alert webhook failed", exc_info=True)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _admin(user: UserPublic) -> None:
        if user.role != "SUPER_ADMIN":
            raise SdlcError("FORBIDDEN", "Token allocations are managed by administrators; you can see your project's balance")

    async def _scope(self, scope_type: str, scope_id: str) -> str:
        if scope_type == "org":
            return ORG
        if scope_type != "project" or not await self._repo.project_name(scope_id):
            raise SdlcError("NOT_FOUND", "Project not found")
        return pool_id("project", scope_id)


class PoolScheduler:
    """Applies monthly allocations that have come due: checks every 15 minutes (cheap: one indexed query)."""

    def __init__(self, pools: TokenPoolService) -> None:
        self._pools = pools
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._loop(), name="token-pool-schedules")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                n = await self._pools.apply_due_schedules()
                if n:
                    log.info("applied %d monthly token allocation(s)", n)
            except Exception:  # noqa: BLE001
                log.error("monthly token allocation tick failed", exc_info=True)
            await asyncio.sleep(900)
