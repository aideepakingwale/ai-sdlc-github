"""Postgres access for token pools, grants and monthly schedules (migration 0053). Services never write SQL elsewhere.

`apply_consumption` is called from inside the ledger's own transaction, so a pool's `consumed_tokens` is always the
sum of the billable ledger rows counted against it since it became metered.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import asyncpg

ORG = "org"
_COLS = "id, scope_type, scope_id, metered, metered_since, allocated_tokens, consumed_tokens, balance_tokens, reference_tokens, alert_level, updated_at"


def month_start(now: datetime) -> datetime:
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc)


def next_month_start(now: datetime) -> datetime:
    return datetime(now.year + (now.month == 12), now.month % 12 + 1, 1, tzinfo=timezone.utc)


def pool_id(scope_type: str, scope_id: str) -> str:
    return ORG if scope_type == "org" else scope_id


def total_of(r: dict[str, Any]) -> int:
    return int(r["input_tokens"]) + int(r["cache_read_tokens"]) + int(r["cache_write_tokens"]) + int(r["output_tokens"])


async def apply_consumption(conn: asyncpg.Connection, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Count the billable tokens in `rows` against the metered pools they belong to (their project, and the organisation).
    Runs in the caller's transaction. Returns the new state of every pool that changed."""
    per_project: dict[str, int] = {}
    total = 0
    for r in rows:
        if not r.get("billable", True):
            continue
        t = total_of(r)
        if t <= 0:
            continue
        total += t
        if r.get("project_id"):
            per_project[r["project_id"]] = per_project.get(r["project_id"], 0) + t
    states: list[dict[str, Any]] = []
    targets = [(ORG, total)] + sorted(per_project.items())          # a fixed order: concurrent writers cannot deadlock on each other
    for pid, n in targets:
        if n <= 0:
            continue
        row = await conn.fetchrow(
            f"UPDATE token_pools SET consumed_tokens = consumed_tokens + $2, updated_at = now() WHERE id = $1 AND metered RETURNING {_COLS}", pid, n)
        if row:
            states.append(dict(row))
    return states


class PoolRepo:
    def __init__(self, db: Any) -> None:
        self._db = db

    @property
    def _p(self) -> asyncpg.Pool:
        assert self._db.pool
        return self._db.pool

    # ------------------------------------------------------------------ reads
    async def get(self, pid: str) -> dict[str, Any] | None:
        r = await self._p.fetchrow(f"SELECT {_COLS} FROM token_pools WHERE id=$1", pid)
        return dict(r) if r else None

    async def projects(self) -> list[dict[str, Any]]:
        """Every live project with its pool (unmetered ones have no pool row yet)."""
        rows = await self._p.fetch(
            f"""SELECT p.id AS project_id, p.name, tp.metered, tp.metered_since, COALESCE(tp.allocated_tokens, 0) AS allocated_tokens,
                       COALESCE(tp.consumed_tokens, 0) AS consumed_tokens, COALESCE(tp.balance_tokens, 0) AS balance_tokens,
                       COALESCE(tp.reference_tokens, 0) AS reference_tokens, COALESCE(tp.alert_level, 0) AS alert_level,
                       COALESCE((SELECT sum(total_tokens) FROM llm_usage_events e WHERE e.project_id = p.id AND e.billable AND e.ts >= now() - interval '30 days'), 0)::bigint AS used_30d
                FROM projects p LEFT JOIN token_pools tp ON tp.id = p.id AND tp.scope_type = 'project' ORDER BY p.name""")
        return [dict(r) for r in rows]

    async def project_name(self, project_id: str) -> str | None:
        return await self._p.fetchval("SELECT name FROM projects WHERE id=$1", project_id)

    async def grants(self, pid: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self._p.fetch(
            """SELECT g.id, g.tokens, g.kind, g.schedule_id, g.granted_by, g.note, g.created_at, u.display_name, u.email
               FROM token_grants g LEFT JOIN users u ON u.id = g.granted_by WHERE g.pool_id=$1 ORDER BY g.created_at DESC LIMIT $2""", pid, limit)
        return [dict(r) for r in rows]

    async def schedules(self, pid: str) -> list[dict[str, Any]]:
        rows = await self._p.fetch("SELECT id, pool_id, tokens, next_run_at, note, created_at FROM token_schedules WHERE pool_id=$1 AND active ORDER BY created_at", pid)
        return [dict(r) for r in rows]

    async def schedule(self, sid: str) -> dict[str, Any] | None:
        r = await self._p.fetchrow("SELECT * FROM token_schedules WHERE id=$1", sid)
        return dict(r) if r else None

    async def burn_per_day(self, pid: str, days: int = 7) -> float:
        """Billable tokens a day over the last `days` (the whole organisation for the org pool)."""
        where, args = ("", [days])
        if pid != ORG:
            where, args = ("AND project_id = $2", [days, pid])
        n = await self._p.fetchval(f"SELECT COALESCE(sum(total_tokens), 0) FROM llm_usage_events WHERE billable AND ts >= now() - make_interval(days => $1) {where}", *args)
        return float(n) / days

    async def ledger_since(self, pid: str, since: datetime) -> int:
        """What the pool should have consumed: the billable ledger since it became metered."""
        where, args = ("", [since])
        if pid != ORG:
            where, args = ("AND project_id = $2", [since, pid])
        return int(await self._p.fetchval(f"SELECT COALESCE(sum(total_tokens), 0) FROM llm_usage_events WHERE billable AND ts >= $1 {where}", *args))

    async def allocated_to_projects(self) -> int:
        return int(await self._p.fetchval(
            "SELECT COALESCE(sum(tp.allocated_tokens), 0) FROM token_pools tp JOIN projects p ON p.id = tp.id WHERE tp.scope_type='project' AND tp.metered") or 0)

    # ------------------------------------------------------------------ writes
    async def grant(self, *, scope_type: str, scope_id: str, tokens: int, kind: str, by: str | None, note: str | None, count_from: str,
                    level_for: Any, schedule_id: str | None = None, now: datetime | None = None) -> dict[str, Any]:
        """Add `tokens` to a pool (creating and metering it on first use), record the grant, and reset the alert level to what the new balance deserves."""
        now = now or datetime.now(timezone.utc)
        pid = pool_id(scope_type, scope_id)
        async with self._p.acquire() as conn, conn.transaction():
            await conn.execute("INSERT INTO token_pools (id, scope_type, scope_id) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING", pid, scope_type, scope_id)
            pool = dict(await conn.fetchrow(f"SELECT {_COLS} FROM token_pools WHERE id=$1 FOR UPDATE", pid))
            consumed = pool["consumed_tokens"]
            since = pool["metered_since"]
            if not pool["metered"]:
                since = month_start(now) if count_from == "month_start" else now
                where, args = ("", [since])
                if pid != ORG:
                    where, args = ("AND project_id = $2", [since, pid])
                consumed = int(await conn.fetchval(f"SELECT COALESCE(sum(total_tokens), 0) FROM llm_usage_events WHERE billable AND ts >= $1 {where}", *args))
            allocated = pool["allocated_tokens"] + tokens
            reference = tokens if tokens > 0 else pool["reference_tokens"]
            level = level_for(allocated - consumed, reference)
            row = await conn.fetchrow(
                f"""UPDATE token_pools SET metered = TRUE, metered_since = $2, allocated_tokens = $3, consumed_tokens = $4, reference_tokens = $5,
                           alert_level = $6, updated_at = now() WHERE id = $1 RETURNING {_COLS}""", pid, since, allocated, consumed, reference, level)
            await conn.execute("INSERT INTO token_grants (id, pool_id, tokens, kind, schedule_id, granted_by, note) VALUES ($1,$2,$3,$4,$5,$6,$7)",
                               str(uuid.uuid4()), pid, tokens, kind, schedule_id, by, note)
            return dict(row)

    async def add_schedule(self, *, scope_type: str, scope_id: str, tokens: int, by: str | None, note: str | None, next_run_at: datetime) -> str:
        pid = pool_id(scope_type, scope_id)
        await self._p.execute("INSERT INTO token_pools (id, scope_type, scope_id) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING", pid, scope_type, scope_id)
        sid = str(uuid.uuid4())
        await self._p.execute("INSERT INTO token_schedules (id, pool_id, tokens, next_run_at, created_by, note) VALUES ($1,$2,$3,$4,$5,$6)", sid, pid, tokens, next_run_at, by, note)
        return sid

    async def stop_schedule(self, sid: str) -> bool:
        return (await self._p.execute("UPDATE token_schedules SET active = FALSE, stopped_at = now() WHERE id=$1 AND active", sid)).endswith("1")

    async def claim_due_schedules(self, now: datetime) -> list[dict[str, Any]]:
        """Schedules due now, each advanced to the first of next month in the same statement: only one caller can claim a given run."""
        rows = await self._p.fetch(
            """UPDATE token_schedules SET next_run_at = date_trunc('month', $1::timestamptz AT TIME ZONE 'UTC' + interval '1 month') AT TIME ZONE 'UTC'
               WHERE active AND next_run_at <= $1 RETURNING id, pool_id, tokens, note, created_by""", now)
        return [dict(r) for r in rows]

    async def pool_scope(self, pid: str) -> tuple[str, str] | None:
        r = await self._p.fetchrow("SELECT scope_type, scope_id FROM token_pools WHERE id=$1", pid)
        return (r["scope_type"], r["scope_id"]) if r else None

    async def set_consumed(self, pid: str, consumed: int, level: int) -> dict[str, Any]:
        return dict(await self._p.fetchrow(f"UPDATE token_pools SET consumed_tokens=$2, alert_level=$3, updated_at=now() WHERE id=$1 RETURNING {_COLS}", pid, consumed, level))

    async def claim_alert(self, pid: str, level: int) -> bool:
        """Announce `level` once: true for the caller that moved the pool up to it."""
        return (await self._p.execute("UPDATE token_pools SET alert_level=$2 WHERE id=$1 AND alert_level < $2", pid, level)).endswith("1")


    # ------------------------------------------------------------------ the gate
    async def gate_state(self, project_id: str | None) -> dict[str, dict[str, Any] | None]:
        """The pools that decide whether work may start: the project's (if any) and the organisation's. A missing row means unmetered."""
        ids = [ORG] + ([project_id] if project_id else [])
        rows = await self._p.fetch("SELECT id, metered, allocated_tokens, consumed_tokens, balance_tokens FROM token_pools WHERE id = ANY($1::text[])", ids)
        by = {r["id"]: dict(r) for r in rows}
        return {"org": by.get(ORG), "project": by.get(project_id) if project_id else None}

    async def record_block(self, *, project_id: str | None, user_id: str | None, pool: str, balance: int, entry: str, enforced: bool) -> None:
        await self._p.execute("INSERT INTO token_gate_blocks (project_id, user_id, pool, balance, entry, enforced) VALUES ($1,$2,$3,$4,$5,$6)",
                              project_id, user_id, pool, balance, entry[:120], enforced)

    async def blocks_by_project(self, days: int = 7) -> dict[str, int]:
        rows = await self._p.fetch(
            "SELECT project_id, count(*)::int AS n FROM token_gate_blocks WHERE ts >= now() - make_interval(days => $1) AND enforced AND project_id IS NOT NULL GROUP BY 1", days)
        return {r["project_id"]: r["n"] for r in rows}

    # ------------------------------------------------------------------ requests for more tokens
    async def open_request(self, project_id: str, user_id: str) -> dict[str, Any] | None:
        r = await self._p.fetchrow("SELECT * FROM token_requests WHERE project_id=$1 AND requested_by=$2 AND status='open'", project_id, user_id)
        return dict(r) if r else None

    async def add_request(self, project_id: str, user_id: str, note: str | None) -> dict[str, Any]:
        r = await self._p.fetchrow("INSERT INTO token_requests (id, project_id, requested_by, note) VALUES ($1,$2,$3,$4) RETURNING *", str(uuid.uuid4()), project_id, user_id, note)
        return dict(r)

    async def open_requests(self) -> list[dict[str, Any]]:
        rows = await self._p.fetch(
            """SELECT r.id, r.project_id, p.name AS project_name, r.requested_by, u.display_name, u.email, r.note, r.created_at
               FROM token_requests r JOIN projects p ON p.id = r.project_id LEFT JOIN users u ON u.id = r.requested_by
               WHERE r.status = 'open' ORDER BY r.created_at""")
        return [dict(r) for r in rows]

    async def resolve_request(self, rid: str, status: str, by: str) -> bool:
        return (await self._p.execute("UPDATE token_requests SET status=$2, resolved_by=$3, resolved_at=now() WHERE id=$1 AND status='open'", rid, status, by)).endswith("1")

    async def resolve_project_requests(self, project_id: str, status: str, by: str) -> int:
        out = await self._p.execute("UPDATE token_requests SET status=$2, resolved_by=$3, resolved_at=now() WHERE project_id=$1 AND status='open'", project_id, status, by)
        return int(out.split()[-1])
