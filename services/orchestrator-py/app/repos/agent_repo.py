"""Postgres access for custom agent and skill definitions (migration 0047). Services never write SQL elsewhere.

Rows come back as plain dicts. `body` and `audit` are JSON documents (the pool's jsonb codec takes and returns dicts).
"""
from __future__ import annotations

from typing import Any

import asyncpg


def _d(r: asyncpg.Record | None) -> dict[str, Any] | None:
    return dict(r) if r else None


class AgentRepo:
    def __init__(self, db: Any) -> None:
        self._db = db

    @property
    def _p(self) -> asyncpg.Pool:
        assert self._db.pool
        return self._db.pool

    # ------------------------------------------------------------ definitions
    async def insert_def(self, d: dict[str, Any]) -> dict[str, Any]:
        r = await self._p.fetchrow(
            """INSERT INTO agent_defs (id, kind, scope, project_id, name, open, source_kind, source_id, source_name, source_version, created_by, created_by_name)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) RETURNING *""",
            d["id"], d["kind"], d["scope"], d.get("project_id"), d["name"], bool(d.get("open", False)), d.get("source_kind"), d.get("source_id"),
            d.get("source_name"), d.get("source_version"), d.get("created_by"), d.get("created_by_name"))
        return dict(r)

    async def get_def(self, def_id: str) -> dict[str, Any] | None:
        return _d(await self._p.fetchrow("SELECT * FROM agent_defs WHERE id=$1", def_id))

    async def list_defs(self, *, scope: str | None = None, kind: str | None = None, project_id: str | None = None,
                        open_only: bool = False, include_retired: bool = False) -> list[dict[str, Any]]:
        where, args = ["TRUE"], []
        for col, val in (("scope", scope), ("kind", kind), ("project_id", project_id)):
            if val is not None:
                args.append(val)
                where.append(f"{col}=${len(args)}")
        if open_only:
            where.append("open")
        if not include_retired:
            where.append("NOT retired")
        rows = await self._p.fetch(f"SELECT * FROM agent_defs WHERE {' AND '.join(where)} ORDER BY lower(name)", *args)
        return [dict(r) for r in rows]

    async def update_def(self, def_id: str, **fields: Any) -> None:
        allowed = {"name", "open", "retired"}
        sets, args = ["updated_at=now()"], [def_id]
        for k, v in fields.items():
            if k in allowed:
                args.append(v)
                sets.append(f"{k}=${len(args)}")
        await self._p.execute(f"UPDATE agent_defs SET {', '.join(sets)} WHERE id=$1", *args)

    async def delete_def(self, def_id: str) -> None:
        await self._p.execute("DELETE FROM agent_defs WHERE id=$1", def_id)

    async def count_defs_by_project(self) -> dict[str, int]:
        rows = await self._p.fetch("SELECT project_id, count(*) AS n FROM agent_defs WHERE scope='project' AND NOT retired GROUP BY project_id")
        return {r["project_id"]: int(r["n"]) for r in rows}

    # ------------------------------------------------------------ versions
    async def insert_version(self, def_id: str, version: int, status: str, body: dict[str, Any], *, author: str | None, author_name: str | None) -> dict[str, Any]:
        r = await self._p.fetchrow(
            "INSERT INTO agent_def_versions (def_id, version, status, body, author, author_name) VALUES ($1,$2,$3,$4,$5,$6) RETURNING *",
            def_id, version, status, body, author, author_name)
        return dict(r)

    async def get_version(self, def_id: str, version: int) -> dict[str, Any] | None:
        return _d(await self._p.fetchrow("SELECT * FROM agent_def_versions WHERE def_id=$1 AND version=$2", def_id, version))

    async def list_versions(self, def_id: str) -> list[dict[str, Any]]:
        rows = await self._p.fetch("SELECT * FROM agent_def_versions WHERE def_id=$1 ORDER BY version DESC", def_id)
        return [dict(r) for r in rows]

    async def update_version(self, def_id: str, version: int, **fields: Any) -> None:
        allowed = {"status", "body", "audit", "submitted_by", "submitted_by_name", "submitted_at", "decided_by", "decided_by_name", "decided_at", "decision_comment"}
        sets, args = ["updated_at=now()"], [def_id, version]
        for k, v in fields.items():
            if k in allowed:
                args.append(v)
                sets.append(f"{k}=${len(args)}")
        await self._p.execute(f"UPDATE agent_def_versions SET {', '.join(sets)} WHERE def_id=$1 AND version=$2", *args)

    async def pending_versions(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """Versions waiting for a decision, with their definition's identity. `project_id=None` lists organisation definitions."""
        q = ("SELECT v.*, d.kind, d.name, d.scope, d.project_id, d.source_name, d.source_version FROM agent_def_versions v JOIN agent_defs d ON d.id=v.def_id "
             "WHERE v.status='pending' AND NOT d.retired AND ")
        rows = await (self._p.fetch(q + "d.project_id=$1 ORDER BY v.submitted_at", project_id) if project_id
                      else self._p.fetch(q + "d.scope='org' ORDER BY v.submitted_at"))
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ attachments
    async def list_attachments(self, project_id: str, stage_key: str | None = None) -> list[dict[str, Any]]:
        if stage_key is None:
            rows = await self._p.fetch("SELECT * FROM agent_stage_attachments WHERE project_id=$1 ORDER BY stage_key, position", project_id)
        else:
            rows = await self._p.fetch("SELECT * FROM agent_stage_attachments WHERE project_id=$1 AND stage_key=$2 ORDER BY position", project_id, stage_key)
        return [dict(r) for r in rows]

    async def replace_attachments(self, project_id: str, stage_key: str, items: list[dict[str, Any]], user_id: str) -> None:
        async with self._p.acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM agent_stage_attachments WHERE project_id=$1 AND stage_key=$2", project_id, stage_key)
                for i, it in enumerate(items):
                    await conn.execute(
                        "INSERT INTO agent_stage_attachments (project_id, stage_key, def_id, pinned_version, runs, condition, roles, position, created_by) "
                        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                        project_id, stage_key, it["def_id"], int(it["pinned_version"]), it.get("runs", "always"), it.get("condition", ""),
                        list(it.get("roles") or []), i, user_id)

    async def attachments_for_def(self, def_id: str) -> list[dict[str, Any]]:
        rows = await self._p.fetch("SELECT * FROM agent_stage_attachments WHERE def_id=$1", def_id)
        return [dict(r) for r in rows]

    async def drop_attachments_for_def(self, def_id: str, project_id: str | None = None) -> None:
        if project_id:
            await self._p.execute("DELETE FROM agent_stage_attachments WHERE def_id=$1 AND project_id=$2", def_id, project_id)
        else:
            await self._p.execute("DELETE FROM agent_stage_attachments WHERE def_id=$1", def_id)

    # ------------------------------------------------------------ grants
    async def list_grants(self, project_id: str) -> list[dict[str, Any]]:
        rows = await self._p.fetch(
            "SELECT g.*, u.email, u.display_name FROM agent_grants g LEFT JOIN users u ON u.id=g.user_id WHERE g.project_id=$1 ORDER BY u.email", project_id)
        return [dict(r) for r in rows]

    async def get_grant(self, project_id: str, user_id: str) -> dict[str, Any] | None:
        return _d(await self._p.fetchrow("SELECT * FROM agent_grants WHERE project_id=$1 AND user_id=$2", project_id, user_id))

    async def set_grant(self, project_id: str, user_id: str, can_edit: bool, can_approve: bool, granted_by: str) -> None:
        if not (can_edit or can_approve):
            await self._p.execute("DELETE FROM agent_grants WHERE project_id=$1 AND user_id=$2", project_id, user_id)
            return
        await self._p.execute(
            "INSERT INTO agent_grants (project_id, user_id, can_edit, can_approve, granted_by) VALUES ($1,$2,$3,$4,$5) "
            "ON CONFLICT (project_id, user_id) DO UPDATE SET can_edit=$3, can_approve=$4, granted_by=$5, granted_at=now()",
            project_id, user_id, can_edit, can_approve, granted_by)

    # ------------------------------------------------------------ guardrails
    async def list_guardrail_overrides(self) -> dict[str, str]:
        rows = await self._p.fetch("SELECT id, severity FROM agent_guardrails")
        return {r["id"]: r["severity"] for r in rows}

    async def set_guardrail(self, guardrail_id: str, severity: str, user_id: str) -> None:
        await self._p.execute(
            "INSERT INTO agent_guardrails (id, severity, updated_by) VALUES ($1,$2,$3) ON CONFLICT (id) DO UPDATE SET severity=$2, updated_by=$3, updated_at=now()",
            guardrail_id, severity, user_id)

    # ------------------------------------------------------------ test cases
    async def list_cases(self, def_id: str) -> list[dict[str, Any]]:
        rows = await self._p.fetch("SELECT * FROM agent_test_cases WHERE def_id=$1 ORDER BY created_at", def_id)
        return [dict(r) for r in rows]

    async def insert_case(self, case_id: str, def_id: str, name: str, inputs: dict[str, Any], user_id: str) -> dict[str, Any]:
        r = await self._p.fetchrow("INSERT INTO agent_test_cases (id, def_id, name, inputs, created_by) VALUES ($1,$2,$3,$4,$5) RETURNING *",
                                   case_id, def_id, name, inputs, user_id)
        return dict(r)

    async def delete_case(self, def_id: str, case_id: str) -> None:
        await self._p.execute("DELETE FROM agent_test_cases WHERE id=$1 AND def_id=$2", case_id, def_id)

    # ------------------------------------------------------------ usage and limits (migration 0048)
    async def record_usage(self, rows: list[dict[str, Any]]) -> None:
        if rows:
            await self._p.executemany(
                "INSERT INTO agent_usage (def_id, project_id, source, prompt_tokens, completion_tokens) VALUES ($1,$2,$3,$4,$5)",
                [(r["def_id"], r.get("project_id"), r.get("source", ""), int(r["prompt"]), int(r["completion"])) for r in rows])

    async def usage_for_def(self, def_id: str, days: int = 30) -> list[dict[str, Any]]:
        rows = await self._p.fetch(
            "SELECT date_trunc('day', created_at)::date AS day, source, count(*)::int AS runs, sum(prompt_tokens)::int AS prompt, sum(completion_tokens)::int AS completion "
            "FROM agent_usage WHERE def_id=$1 AND created_at > now() - make_interval(days => $2) GROUP BY 1, 2 ORDER BY 1", def_id, days)
        return [dict(r) for r in rows]

    async def usage_for_project(self, project_id: str, since: Any) -> list[dict[str, Any]]:
        rows = await self._p.fetch(
            "SELECT def_id, count(*)::int AS runs, sum(prompt_tokens)::int AS prompt, sum(completion_tokens)::int AS completion "
            "FROM agent_usage WHERE project_id=$1 AND created_at >= $2 GROUP BY def_id", project_id, since)
        return [dict(r) for r in rows]

    async def get_limit(self, project_id: str) -> int | None:
        return await self._p.fetchval("SELECT monthly_tokens FROM agent_project_limits WHERE project_id=$1", project_id)

    async def set_limit(self, project_id: str, monthly_tokens: int | None, user_id: str) -> None:
        if monthly_tokens is None:
            await self._p.execute("DELETE FROM agent_project_limits WHERE project_id=$1", project_id)
            return
        await self._p.execute(
            "INSERT INTO agent_project_limits (project_id, monthly_tokens, updated_by) VALUES ($1,$2,$3) "
            "ON CONFLICT (project_id) DO UPDATE SET monthly_tokens=$2, updated_by=$3, updated_at=now()", project_id, int(monthly_tokens), user_id)

    # ------------------------------------------------------------ runs a person started (migration 0049)
    KEEP_RUNS = 50

    async def insert_run(self, r: dict[str, Any]) -> None:
        await self._p.execute(
            "INSERT INTO agent_runs (id, def_id, version, project_id, stage_key, user_id, user_name, inputs, outputs, warnings, saved, tokens, provider, model) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)",
            r["id"], r["def_id"], r["version"], r["project_id"], r.get("stage_key"), r.get("user_id"), r.get("user_name"), r["inputs"], r["outputs"],
            r.get("warnings", []), r.get("saved", []), int(r.get("tokens", 0)), r.get("provider"), r.get("model"))
        await self._p.execute(           # a project keeps the latest runs of an agent, not every one
            "DELETE FROM agent_runs WHERE id IN (SELECT id FROM agent_runs WHERE project_id=$1 AND def_id=$2 ORDER BY created_at DESC OFFSET $3)",
            r["project_id"], r["def_id"], self.KEEP_RUNS)

    async def list_runs(self, project_id: str, def_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        if def_id:
            rows = await self._p.fetch("SELECT * FROM agent_runs WHERE project_id=$1 AND def_id=$2 ORDER BY created_at DESC LIMIT $3", project_id, def_id, limit)
        else:
            rows = await self._p.fetch("SELECT * FROM agent_runs WHERE project_id=$1 ORDER BY created_at DESC LIMIT $2", project_id, limit)
        return [dict(r) for r in rows]
