"""Reads over the token ledger (`llm_usage_events`, migration 0051). Services never write SQL elsewhere.

Every query takes one `UsageFilter`; the filter builds the WHERE clause so the per-project, per-person and
organisation views cannot drift apart on what they count. All identifiers are fixed here (nothing from a
request is interpolated into SQL).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

_TOTALS = """
  COALESCE(sum(total_tokens), 0)::bigint AS tokens,
  count(*)::bigint AS calls,
  COALESCE(sum(input_tokens), 0)::bigint AS input_tokens,
  COALESCE(sum(cache_read_tokens), 0)::bigint AS cache_read_tokens,
  COALESCE(sum(cache_write_tokens), 0)::bigint AS cache_write_tokens,
  COALESCE(sum(output_tokens), 0)::bigint AS output_tokens,
  COALESCE(sum(total_tokens) FILTER (WHERE status <> 'ok'), 0)::bigint AS waste_tokens,
  COALESCE(sum(total_tokens) FILTER (WHERE estimated), 0)::bigint AS estimated_tokens
"""


@dataclass(frozen=True)
class UsageFilter:
    since: datetime                       # inclusive
    until: datetime                       # exclusive
    project_id: str | None = None
    user_id: str | None = None
    billable: str = "billable"            # billable | offline | all

    def where(self, extra: str = "", a: str = "") -> tuple[str, list[Any]]:
        """`a` is a table alias prefix such as "e." for queries that join."""
        parts = [f"{a}ts >= $1", f"{a}ts < $2"]
        args: list[Any] = [self.since, self.until]
        if self.project_id is not None:
            args.append(self.project_id)
            parts.append(f"{a}project_id = ${len(args)}")
        if self.user_id is not None:
            args.append(self.user_id)
            parts.append(f"{a}user_id = ${len(args)}")
        if self.billable == "billable":
            parts.append(f"{a}billable")
        elif self.billable == "offline":
            parts.append(f"NOT {a}billable")
        if extra:
            parts.append(extra)
        return " AND ".join(parts), args


class UsageRepo:
    def __init__(self, db: Any) -> None:
        self._db = db

    @property
    def _p(self) -> asyncpg.Pool:
        assert self._db.pool
        return self._db.pool

    async def _rows(self, select: str, f: UsageFilter, tail: str, extra: str = "") -> list[dict[str, Any]]:
        where, args = f.where(extra)
        return [dict(r) for r in await self._p.fetch(f"SELECT {select} FROM llm_usage_events WHERE {where} {tail}", *args)]

    async def project_name(self, project_id: str) -> str:
        return str(await self._p.fetchval("SELECT name FROM projects WHERE id=$1", project_id) or "")

    async def totals(self, f: UsageFilter) -> dict[str, Any]:
        where, args = f.where()
        return dict(await self._p.fetchrow(f"SELECT {_TOTALS} FROM llm_usage_events WHERE {where}", *args))

    async def by_day(self, f: UsageFilter) -> list[dict[str, Any]]:
        return await self._rows("(ts AT TIME ZONE 'UTC')::date AS day, sum(total_tokens)::bigint AS tokens, count(*)::bigint AS calls", f, "GROUP BY 1 ORDER BY 1")

    async def by_user(self, f: UsageFilter) -> list[dict[str, Any]]:
        where, args = f.where()
        sql = f"""SELECT e.user_id, u.display_name, u.email, sum(e.total_tokens)::bigint AS tokens, count(*)::bigint AS calls,
                         COALESCE(sum(e.total_tokens) FILTER (WHERE e.status <> 'ok'), 0)::bigint AS waste_tokens
                  FROM (SELECT * FROM llm_usage_events WHERE {where}) e LEFT JOIN users u ON u.id = e.user_id
                  GROUP BY e.user_id, u.display_name, u.email ORDER BY tokens DESC"""
        return [dict(r) for r in await self._p.fetch(sql, *args)]

    async def by_activity(self, f: UsageFilter) -> list[dict[str, Any]]:
        return await self._rows("activity, sum(total_tokens)::bigint AS tokens, count(*)::bigint AS calls, "
                                "COALESCE(sum(total_tokens) FILTER (WHERE status <> 'ok'), 0)::bigint AS waste_tokens", f, "GROUP BY 1 ORDER BY tokens DESC")

    async def by_stage(self, f: UsageFilter) -> list[dict[str, Any]]:
        return await self._rows("stage, sum(total_tokens)::bigint AS tokens, count(*)::bigint AS calls", f, "GROUP BY 1 ORDER BY stage NULLS LAST")

    async def by_model(self, f: UsageFilter) -> list[dict[str, Any]]:
        return await self._rows("provider, model, sum(total_tokens)::bigint AS tokens, count(*)::bigint AS calls", f, "GROUP BY 1,2 ORDER BY tokens DESC")

    async def by_agent(self, f: UsageFilter) -> list[dict[str, Any]]:
        where, args = f.where("agent_id IS NOT NULL")
        sql = f"""SELECT e.agent_id, d.name, sum(e.total_tokens)::bigint AS tokens, count(*)::bigint AS calls
                  FROM (SELECT * FROM llm_usage_events WHERE {where}) e LEFT JOIN agent_defs d ON d.id = e.agent_id
                  GROUP BY e.agent_id, d.name ORDER BY tokens DESC"""
        return [dict(r) for r in await self._p.fetch(sql, *args)]

    async def waste(self, f: UsageFilter) -> list[dict[str, Any]]:
        return await self._rows("activity, status, count(*)::bigint AS calls, sum(total_tokens)::bigint AS tokens", f,
                                "GROUP BY 1,2 ORDER BY tokens DESC LIMIT 20", extra="status <> 'ok'")

    async def by_project(self, f: UsageFilter) -> list[dict[str, Any]]:
        where, args = f.where("project_id IS NOT NULL")
        sql = f"""SELECT e.project_id, p.name, sum(e.total_tokens)::bigint AS tokens, count(*)::bigint AS calls,
                         count(DISTINCT e.user_id)::bigint AS people,
                         COALESCE(sum(e.total_tokens) FILTER (WHERE e.status <> 'ok'), 0)::bigint AS waste_tokens
                  FROM (SELECT * FROM llm_usage_events WHERE {where}) e LEFT JOIN projects p ON p.id = e.project_id
                  GROUP BY e.project_id, p.name ORDER BY tokens DESC"""
        return [dict(r) for r in await self._p.fetch(sql, *args)]

    async def unattributed(self, f: UsageFilter) -> dict[str, Any]:
        where, args = f.where("(project_id IS NULL OR user_id IS NULL)")
        return dict(await self._p.fetchrow(
            f"SELECT count(*)::bigint AS calls, COALESCE(sum(total_tokens), 0)::bigint AS tokens FROM llm_usage_events WHERE {where}", *args))

    async def events(self, f: UsageFilter, *, activity: str | None, status: str | None, limit: int, offset: int) -> list[dict[str, Any]]:
        where, args = f.where(a="e.")
        act_idx = 0
        if activity:
            args.append(activity)
            act_idx = len(args)
            where += f" AND e.activity = ${act_idx}"
        if status == "failed":
            # Anything that went wrong, including a call the provider answered but the application rejected (it has a capture).
            where += " AND (e.status <> 'ok' OR EXISTS (SELECT 1 FROM llm_failure_captures c WHERE c.request_id = e.request_id))"
        elif status:
            args.append(status)
            where += f" AND e.status = ${len(args)}"
        sql = f"""SELECT e.ts, e.request_id, e.attempt_no, e.provider, e.model, e.region, e.provider_request_id, e.project_id, e.stage, e.user_id,
                         u.display_name, u.email, e.actor_type, e.activity, e.tag, e.agent_id, e.run_id, e.input_tokens, e.cache_read_tokens,
                         e.cache_write_tokens, e.output_tokens, e.total_tokens, e.status, e.error_class, e.stop_reason, e.latency_ms, e.estimated, e.billable,
                         EXISTS (SELECT 1 FROM llm_failure_captures c WHERE c.request_id = e.request_id) AS has_capture
                  FROM llm_usage_events e LEFT JOIN users u ON u.id = e.user_id
                  WHERE {where}"""
        if status == "failed" and f.project_id is not None and f.billable != "offline":
            # A request that never reached a provider (the gateway was down, the call was rejected before sending) billed nothing, so the ledger
            # has no row for it, but its text was kept: list it too, as one attempt that used no tokens. The project is always $3 here.
            cond = ["c.created_at >= $1", "c.created_at < $2", "c.project_id = $3"]
            if f.user_id is not None:
                cond.append("c.user_id = $4")
            if act_idx:
                cond.append(f"c.activity = ${act_idx}")
            sql += f"""
                  UNION ALL
                  SELECT c.created_at, c.request_id, 1, '', '', NULL, NULL, c.project_id, c.stage, c.user_id,
                         u.display_name, u.email, CASE WHEN c.user_id IS NULL THEN 'system' ELSE 'user' END, c.activity, c.tag, NULL, NULL, 0, 0,
                         0, 0, 0, 'error', c.reason, NULL, 0, FALSE, TRUE, TRUE
                  FROM llm_failure_captures c LEFT JOIN users u ON u.id = c.user_id
                  WHERE {' AND '.join(cond)} AND NOT EXISTS (SELECT 1 FROM llm_usage_events e2 WHERE e2.request_id = c.request_id)"""
        args += [limit, offset]
        sql = f"SELECT * FROM ({sql}) t ORDER BY ts DESC, attempt_no LIMIT ${len(args) - 1} OFFSET ${len(args)}"
        return [dict(r) for r in await self._p.fetch(sql, *args)]

    async def request_rows(self, project_id: str, request_id: str) -> list[dict[str, Any]]:
        """Every ledger row (attempt) of one request in a project, oldest first."""
        sql = """SELECT e.ts, e.request_id, e.attempt_no, e.provider, e.model, e.region, e.provider_request_id, e.project_id, e.stage, e.user_id,
                        u.display_name, u.email, e.actor_type, e.activity, e.tag, e.agent_id, e.run_id, e.input_tokens, e.cache_read_tokens, e.cache_write_tokens,
                        e.output_tokens, e.total_tokens, e.status, e.error_class, e.stop_reason, e.latency_ms, e.estimated, e.billable
                 FROM llm_usage_events e LEFT JOIN users u ON u.id = e.user_id
                 WHERE e.project_id = $1 AND e.request_id = $2 ORDER BY e.attempt_no, e.ts"""
        return [dict(r) for r in await self._p.fetch(sql, project_id, request_id)]
