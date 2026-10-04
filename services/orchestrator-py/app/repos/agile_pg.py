"""Postgres access for Agile delivery (iterations, releases, stage instances, backlog, proposals).

Mixed into `Database`. All writes that must be atomic (a sprint + its stage slots, a backlog item + its
key) run in one transaction; optimistic concurrency uses the `version` column."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import asyncpg

# Columns of backlog_items that callers may change through `update_backlog_item`.
BACKLOG_FIELDS = {
    "type", "title", "description", "acceptance_criteria", "estimate", "rank", "status", "epic_id",
    "components", "labels", "iteration_id", "jira_key", "jira_updated", "jira_synced_at",
}
ITERATION_FIELDS = {"goal", "status", "capacity", "starts_on", "ends_on", "started_at", "closed_at", "summary"}
AGILE_FIELDS = {"sprint_days", "default_capacity", "wip_limit", "index_strategy", "auto_min_score"}
RANK_STEP = 1024.0


def _id() -> str:
    return str(uuid.uuid4())


class AgileRepo:
    pool: asyncpg.Pool | None  # provided by Database

    # ------------------------------------------------------------------ settings
    async def get_project_agile(self, project_id: str) -> asyncpg.Record | None:
        assert self.pool
        return await self.pool.fetchrow("SELECT * FROM project_agile WHERE project_id=$1", project_id)

    async def insert_project_agile(
        self, *, project_id: str, methodology: str, sprint_days: int, default_capacity: float,
        wip_limit: int | None, index_strategy: str, auto_min_score: int, created_by: str,
    ) -> None:
        assert self.pool
        await self.pool.execute(
            "INSERT INTO project_agile (project_id, methodology, sprint_days, default_capacity, wip_limit, "
            "index_strategy, auto_min_score, created_by) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
            project_id, methodology, sprint_days, default_capacity, wip_limit, index_strategy, auto_min_score,
            created_by,
        )

    async def update_project_agile(self, project_id: str, **fields: Any) -> asyncpg.Record | None:
        assert self.pool
        bad = set(fields) - AGILE_FIELDS
        if bad:
            raise ValueError(f"not updatable: {sorted(bad)}")
        if not fields:
            return await self.get_project_agile(project_id)
        sets = ", ".join(f"{k}=${i + 2}" for i, k in enumerate(fields))
        return await self.pool.fetchrow(
            f"UPDATE project_agile SET {sets}, updated_at=now() WHERE project_id=$1 RETURNING *",
            project_id, *fields.values(),
        )

    # ------------------------------------------------------------------ releases
    async def list_releases(self, project_id: str) -> list[asyncpg.Record]:
        assert self.pool
        return await self.pool.fetch("SELECT * FROM releases WHERE project_id=$1 ORDER BY number", project_id)

    async def get_release(self, release_id: str) -> asyncpg.Record | None:
        assert self.pool
        return await self.pool.fetchrow("SELECT * FROM releases WHERE id=$1", release_id)

    async def insert_release(self, *, project_id: str, name: str, goal: str = "") -> asyncpg.Record:
        """Next release number for the project (allocated atomically)."""
        assert self.pool
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"rel:{project_id}")
            n = await conn.fetchval("SELECT COALESCE(MAX(number),0)+1 FROM releases WHERE project_id=$1", project_id)
            return await conn.fetchrow(
                "INSERT INTO releases (id, project_id, number, code, name, goal) VALUES ($1,$2,$3,$4,$5,$6) "
                "RETURNING *", _id(), project_id, n, f"R-{n:03d}", name, goal,
            )

    async def set_release_status(self, release_id: str, status: str) -> None:
        assert self.pool
        await self.pool.execute(
            "UPDATE releases SET status=$2, closed_at = CASE WHEN $2='closed' THEN now() ELSE closed_at END "
            "WHERE id=$1", release_id, status,
        )

    # ------------------------------------------------------------------ iterations + stage instances
    async def list_iterations(self, project_id: str) -> list[asyncpg.Record]:
        assert self.pool
        return await self.pool.fetch("SELECT * FROM iterations WHERE project_id=$1 ORDER BY number", project_id)

    async def get_iteration(self, iteration_id: str) -> asyncpg.Record | None:
        assert self.pool
        return await self.pool.fetchrow("SELECT * FROM iterations WHERE id=$1", iteration_id)

    async def get_open_iteration(self, project_id: str) -> asyncpg.Record | None:
        assert self.pool
        return await self.pool.fetchrow(
            "SELECT * FROM iterations WHERE project_id=$1 AND status IN ('planned','active')", project_id)

    async def list_stage_instances(self, project_id: str) -> list[asyncpg.Record]:
        assert self.pool
        return await self.pool.fetch("SELECT * FROM stage_instances WHERE project_id=$1 ORDER BY seq", project_id)

    async def create_iteration_with_instances(
        self, *, project_id: str, release_id: str, goal: str, capacity: float, status: str,
        starts_on: dt.date | None, ends_on: dt.date | None,
        slots_for: Any,  # callable(existing_instances:list[Record]) -> list[(seq, key, base_key)] given the label
    ) -> asyncpg.Record:
        """One transaction: allocate the next sprint number/label, ask `slots_for(label, number, existing)` for
        the stage slots, insert the sprint and its slots. The partial unique index rejects a second open sprint
        even under a race (UniqueViolationError → caller maps it to a conflict)."""
        assert self.pool
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"iter:{project_id}")
            n = await conn.fetchval("SELECT COALESCE(MAX(number),0)+1 FROM iterations WHERE project_id=$1", project_id)
            label = f"S-{n:03d}"
            existing = await conn.fetch("SELECT * FROM stage_instances WHERE project_id=$1", project_id)
            row = await conn.fetchrow(
                "INSERT INTO iterations (id, project_id, release_id, number, label, goal, status, capacity, "
                "starts_on, ends_on, started_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10, "
                "CASE WHEN $7='active' THEN now() END) RETURNING *",
                _id(), project_id, release_id, n, label, goal, status, capacity, starts_on, ends_on,
            )
            for seq, key, base_key in slots_for(label, n, existing):
                await conn.execute(
                    "INSERT INTO stage_instances (project_id, seq, key, base_key, scope, iteration_id) "
                    "VALUES ($1,$2,$3,$4,'iteration',$5)", project_id, seq, key, base_key, row["id"],
                )
            return row

    async def create_release_instances(
        self, *, project_id: str, release_id: str, slots_for: Any,
    ) -> list[asyncpg.Record]:
        assert self.pool
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"iter:{project_id}")
            rel = await conn.fetchrow("SELECT * FROM releases WHERE id=$1 FOR UPDATE", release_id)
            if rel is None:
                raise LookupError("release not found")
            if rel["status"] != "open":
                raise ValueError(f"release is {rel['status']}")
            existing = await conn.fetch("SELECT * FROM stage_instances WHERE project_id=$1", project_id)
            for seq, key, base_key in slots_for(rel["code"], rel["number"], existing):
                await conn.execute(
                    "INSERT INTO stage_instances (project_id, seq, key, base_key, scope, release_id) "
                    "VALUES ($1,$2,$3,$4,'release',$5)", project_id, seq, key, base_key, release_id,
                )
            await conn.execute("UPDATE releases SET status='hardening' WHERE id=$1", release_id)
            return await conn.fetch("SELECT * FROM stage_instances WHERE release_id=$1 ORDER BY seq", release_id)

    async def update_iteration(self, iteration_id: str, **fields: Any) -> asyncpg.Record | None:
        assert self.pool
        bad = set(fields) - ITERATION_FIELDS
        if bad:
            raise ValueError(f"not updatable: {sorted(bad)}")
        if not fields:
            return await self.get_iteration(iteration_id)
        sets = ", ".join(f"{k}=${i + 2}" for i, k in enumerate(fields))
        return await self.pool.fetchrow(
            f"UPDATE iterations SET {sets} WHERE id=$1 RETURNING *", iteration_id, *fields.values())

    async def delete_iteration(self, iteration_id: str) -> None:
        assert self.pool
        await self.pool.execute("DELETE FROM iterations WHERE id=$1", iteration_id)

    # ------------------------------------------------------------------ backlog
    async def list_backlog(
        self, project_id: str, *, statuses: list[str] | None = None, iteration_id: str | None = None,
        limit: int = 2000,
    ) -> list[asyncpg.Record]:
        assert self.pool
        sql, args = "SELECT * FROM backlog_items WHERE project_id=$1", [project_id]
        if statuses:
            args.append(statuses)
            sql += f" AND status = ANY(${len(args)})"
        if iteration_id:
            args.append(iteration_id)
            sql += f" AND iteration_id=${len(args)}"
        args.append(limit)
        return await self.pool.fetch(sql + f" ORDER BY rank, created_at LIMIT ${len(args)}", *args)

    async def get_backlog_item(self, project_id: str, item_id: str) -> asyncpg.Record | None:
        assert self.pool
        return await self.pool.fetchrow(
            "SELECT * FROM backlog_items WHERE project_id=$1 AND (id=$2 OR item_key=$2)", project_id, item_id)

    async def get_backlog_by_jira(self, project_id: str, jira_key: str) -> asyncpg.Record | None:
        assert self.pool
        return await self.pool.fetchrow(
            "SELECT * FROM backlog_items WHERE project_id=$1 AND jira_key=$2", project_id, jira_key)

    async def insert_backlog_item(self, *, project_id: str, created_by: str | None, **f: Any) -> asyncpg.Record:
        """Allocates the per-project key (DM-n) atomically and appends to the end of the ranking by default."""
        assert self.pool
        bad = set(f) - BACKLOG_FIELDS
        if bad:
            raise ValueError(f"not settable: {sorted(bad)}")
        async with self.pool.acquire() as conn, conn.transaction():
            n = await conn.fetchval(
                "INSERT INTO backlog_counters (project_id, next_item) VALUES ($1, 2) "
                "ON CONFLICT (project_id) DO UPDATE SET next_item = backlog_counters.next_item + 1 "
                "RETURNING next_item - 1", project_id)
            if "rank" not in f:
                top = await conn.fetchval(
                    "SELECT COALESCE(MAX(rank),0) FROM backlog_items WHERE project_id=$1", project_id)
                f["rank"] = float(top) + RANK_STEP
            cols = ["id", "project_id", "item_key", "created_by", *f]
            vals = [_id(), project_id, f"DM-{n}", created_by, *f.values()]
            ph = ", ".join(f"${i + 1}" for i in range(len(cols)))
            return await conn.fetchrow(
                f"INSERT INTO backlog_items ({', '.join(cols)}) VALUES ({ph}) RETURNING *", *vals)

    async def update_backlog_item(
        self, project_id: str, item_id: str, *, expected_version: int | None, **f: Any,
    ) -> asyncpg.Record | None:
        """Returns the updated row, or None when `expected_version` no longer matches (lost update)."""
        assert self.pool
        bad = set(f) - BACKLOG_FIELDS
        if bad:
            raise ValueError(f"not updatable: {sorted(bad)}")
        sets = ", ".join(f"{k}=${i + 3}" for i, k in enumerate(f))
        sets = (sets + ", " if sets else "") + "version=version+1, updated_at=now()"
        args: list[Any] = [project_id, item_id, *f.values()]
        where = "project_id=$1 AND id=$2"
        if expected_version is not None:
            args.append(expected_version)
            where += f" AND version=${len(args)}"
        return await self.pool.fetchrow(f"UPDATE backlog_items SET {sets} WHERE {where} RETURNING *", *args)

    async def assign_items_to_iteration(
        self, project_id: str, item_ids: list[str], iteration_id: str, status: str,
    ) -> int:
        assert self.pool
        r = await self.pool.execute(
            "UPDATE backlog_items SET iteration_id=$3, status=$4, version=version+1, updated_at=now() "
            "WHERE project_id=$1 AND id = ANY($2)", project_id, item_ids, iteration_id, status)
        return int(r.split()[-1])

    async def release_unfinished_items(self, iteration_id: str, to_status: str = "ready") -> list[str]:
        """At sprint close: everything not done goes back to the backlog (ids returned for the digest)."""
        assert self.pool
        rows = await self.pool.fetch(
            "UPDATE backlog_items SET iteration_id=NULL, status=$2, version=version+1, updated_at=now() "
            "WHERE iteration_id=$1 AND status IN ('in_sprint','in_progress') RETURNING id", iteration_id, to_status)
        return [r["id"] for r in rows]

    async def count_backlog_by_status(self, project_id: str) -> dict[str, int]:
        assert self.pool
        rows = await self.pool.fetch(
            "SELECT status, COUNT(*) c FROM backlog_items WHERE project_id=$1 GROUP BY status", project_id)
        return {r["status"]: int(r["c"]) for r in rows}

    # ------------------------------------------------------------------ proposals
    async def insert_proposal(
        self, *, project_id: str, iteration_id: str | None, phase: int, kind: str,
        payload: dict[str, Any], warnings: list[str],
    ) -> asyncpg.Record:
        """A new proposal supersedes any still-open one for the same stage slot + kind."""
        assert self.pool
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE agile_proposals SET status='superseded' WHERE project_id=$1 AND phase=$2 AND kind=$3 "
                "AND status='proposed'", project_id, phase, kind)
            return await conn.fetchrow(
                "INSERT INTO agile_proposals (id, project_id, iteration_id, phase, kind, payload, warnings) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING *",
                _id(), project_id, iteration_id, phase, kind, payload, warnings)

    async def get_proposal(self, proposal_id: str) -> asyncpg.Record | None:
        assert self.pool
        return await self.pool.fetchrow("SELECT * FROM agile_proposals WHERE id=$1", proposal_id)

    async def latest_proposal(self, project_id: str, phase: int, kind: str) -> asyncpg.Record | None:
        assert self.pool
        return await self.pool.fetchrow(
            "SELECT * FROM agile_proposals WHERE project_id=$1 AND phase=$2 AND kind=$3 AND status <> 'superseded' "
            "ORDER BY created_at DESC LIMIT 1", project_id, phase, kind)

    async def update_proposal(
        self, proposal_id: str, *, expected_version: int, payload: dict[str, Any], warnings: list[str],
    ) -> asyncpg.Record | None:
        assert self.pool
        return await self.pool.fetchrow(
            "UPDATE agile_proposals SET payload=$3, warnings=$4, version=version+1 "
            "WHERE id=$1 AND version=$2 AND status='proposed' RETURNING *",
            proposal_id, expected_version, payload, warnings)

    async def set_proposal_status(self, proposal_id: str, status: str, decided_by: str | None) -> bool:
        """Only a still-`proposed` row can be decided (makes applying idempotent and race-safe)."""
        assert self.pool
        r = await self.pool.execute(
            "UPDATE agile_proposals SET status=$2, decided_at=now(), decided_by=$3 "
            "WHERE id=$1 AND status='proposed'", proposal_id, status, decided_by)
        return r.endswith(" 1")

    # ------------------------------------------------------------------ Jira sync state
    async def get_sync_state(self, project_id: str) -> asyncpg.Record | None:
        assert self.pool
        return await self.pool.fetchrow("SELECT * FROM agile_sync_state WHERE project_id=$1", project_id)

    async def save_sync_state(
        self, project_id: str, *, watermark: dt.datetime | None, status: str, error: str | None,
        stats: dict[str, Any],
    ) -> None:
        assert self.pool
        await self.pool.execute(
            "INSERT INTO agile_sync_state (project_id, watermark, last_run_at, last_status, last_error, stats) "
            "VALUES ($1,$2,now(),$3,$4,$5) ON CONFLICT (project_id) DO UPDATE SET "
            "watermark=COALESCE(EXCLUDED.watermark, agile_sync_state.watermark), last_run_at=now(), "
            "last_status=EXCLUDED.last_status, last_error=EXCLUDED.last_error, stats=EXCLUDED.stats",
            project_id, watermark, status, error, stats)
