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
RELEASE_FIELDS = {"name", "goal", "intake_rule", "use_pool", "setup"}
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

    @staticmethod
    async def _insert_release(
        conn: asyncpg.Connection, project_id: str, name: str, goal: str = "", *, forked_from: str | None = None,
        fork_baseline: dict[str, Any] | None = None, setup: dict[str, Any] | None = None,
        intake_rule: str = "pool", use_pool: bool = True, created_by: str | None = None,
    ) -> asyncpg.Record:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"rel:{project_id}")
        n = await conn.fetchval("SELECT COALESCE(MAX(number),0)+1 FROM releases WHERE project_id=$1", project_id)
        return await conn.fetchrow(
            "INSERT INTO releases (id, project_id, number, code, name, goal, forked_from, fork_baseline, setup, "
            "intake_rule, use_pool, created_by) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) RETURNING *",
            _id(), project_id, n, f"R-{n:03d}", name, goal, forked_from, fork_baseline or {}, setup or {},
            intake_rule, use_pool, created_by,
        )

    async def insert_release(self, *, project_id: str, name: str, goal: str = "", **kw: Any) -> asyncpg.Record:
        """Next release number for the project (allocated atomically)."""
        assert self.pool
        async with self.pool.acquire() as conn, conn.transaction():
            return await self._insert_release(conn, project_id, name, goal, **kw)

    async def close_release_and_open_next(
        self, project_id: str, release_id: str,
    ) -> tuple[asyncpg.Record | None, asyncpg.Record | None]:
        """Close a HARDENING release in ONE transaction. When no other release is live (open/hardening) the next
        one is opened in the same transaction, so a project is never left without a live release; when a parallel
        release is live, nothing is opened. A replay or a concurrent call closes at most once.
        Returns (closed, opened); closed is None when there was nothing to do."""
        assert self.pool
        async with self.pool.acquire() as conn, conn.transaction():
            closed = await conn.fetchrow(
                "UPDATE releases SET status='closed', closed_at=now() WHERE id=$1 AND project_id=$2 AND status='hardening' "
                "RETURNING *", release_id, project_id)
            if closed is None:
                return None, None
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"rel:{project_id}")
            live = await conn.fetchval(
                "SELECT COUNT(*) FROM releases WHERE project_id=$1 AND status IN ('open','hardening')", project_id)
            if live:
                return closed, None
            return closed, await self._insert_release(conn, project_id, f"Release {closed['number'] + 1}")

    async def update_release(self, release_id: str, **fields: Any) -> asyncpg.Record | None:
        assert self.pool
        bad = set(fields) - RELEASE_FIELDS
        if bad:
            raise ValueError(f"not updatable: {sorted(bad)}")
        if not fields:
            return await self.get_release(release_id)
        sets = ", ".join(f"{k}=${i + 2}" for i, k in enumerate(fields))
        return await self.pool.fetchrow(f"UPDATE releases SET {sets} WHERE id=$1 RETURNING *", release_id, *fields.values())

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

    async def get_open_iteration(self, project_id: str, release_id: str | None = None) -> asyncpg.Record | None:
        """The open (planned/active) sprint of a release. Without a release: the project's only open sprint, or the
        earliest one when several releases run in parallel."""
        assert self.pool
        if release_id:
            return await self.pool.fetchrow(
                "SELECT * FROM iterations WHERE project_id=$1 AND release_id=$2 AND status IN ('planned','active')",
                project_id, release_id)
        return await self.pool.fetchrow(
            "SELECT * FROM iterations WHERE project_id=$1 AND status IN ('planned','active') ORDER BY number LIMIT 1",
            project_id)

    async def list_open_iterations(self, project_id: str) -> list[asyncpg.Record]:
        assert self.pool
        return await self.pool.fetch(
            "SELECT * FROM iterations WHERE project_id=$1 AND status IN ('planned','active') ORDER BY number", project_id)

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
            rel_status = await conn.fetchval("SELECT status FROM releases WHERE id=$1 FOR UPDATE", release_id)
            if rel_status != "open":          # a concurrent hardening may have started since the caller looked
                raise ValueError(f"the release is {rel_status or 'missing'}, not open")
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

    @staticmethod
    async def _insert_item(conn: asyncpg.Connection, project_id: str, created_by: str | None, f: dict[str, Any]) -> asyncpg.Record:
        """Allocates the per-project key (DM-n) atomically and appends to the end of the ranking by default."""
        bad = set(f) - BACKLOG_FIELDS
        if bad:
            raise ValueError(f"not settable: {sorted(bad)}")
        n = await conn.fetchval(
            "INSERT INTO backlog_counters (project_id, next_item) VALUES ($1, 2) "
            "ON CONFLICT (project_id) DO UPDATE SET next_item = backlog_counters.next_item + 1 "
            "RETURNING next_item - 1", project_id)
        if "rank" not in f:
            top = await conn.fetchval("SELECT COALESCE(MAX(rank),0) FROM backlog_items WHERE project_id=$1", project_id)
            f = {**f, "rank": float(top) + RANK_STEP}
        cols = ["id", "project_id", "item_key", "created_by", *f]
        vals = [_id(), project_id, f"DM-{n}", created_by, *f.values()]
        ph = ", ".join(f"${i + 1}" for i in range(len(cols)))
        return await conn.fetchrow(f"INSERT INTO backlog_items ({', '.join(cols)}) VALUES ({ph}) RETURNING *", *vals)

    async def insert_backlog_item(self, *, project_id: str, created_by: str | None, **f: Any) -> asyncpg.Record:
        assert self.pool
        async with self.pool.acquire() as conn, conn.transaction():
            return await self._insert_item(conn, project_id, created_by, dict(f))

    async def move_backlog_item(
        self, project_id: str, item_id: str, *, before_id: str | None = None, after_id: str | None = None,
    ) -> asyncpg.Record | None:
        """Re-rank one item next to a neighbour. Rebalances the whole ranking when the gap gets too small."""
        assert self.pool
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"rank:{project_id}")
            item = await conn.fetchrow("SELECT * FROM backlog_items WHERE project_id=$1 AND id=$2", project_id, item_id)
            if item is None:
                return None
            ref_id = before_id or after_id
            ref = await conn.fetchrow(
                "SELECT rank FROM backlog_items WHERE project_id=$1 AND id=$2 AND id<>$3", project_id, ref_id, item_id
            ) if ref_id else None
            if ref_id and ref is None:
                raise LookupError("reference item not found")
            if ref is None:   # no neighbour: move to the very top
                low = await conn.fetchval("SELECT COALESCE(MIN(rank),0) FROM backlog_items WHERE project_id=$1 AND id<>$2",
                                          project_id, item_id)
                new_rank = float(low) - RANK_STEP
            elif before_id:   # directly ABOVE ref → between the previous item and ref
                prev = await conn.fetchval(
                    "SELECT MAX(rank) FROM backlog_items WHERE project_id=$1 AND id<>$2 AND rank < $3",
                    project_id, item_id, ref["rank"])
                new_rank = (float(prev) + ref["rank"]) / 2 if prev is not None else ref["rank"] - RANK_STEP
            else:             # directly BELOW ref
                nxt = await conn.fetchval(
                    "SELECT MIN(rank) FROM backlog_items WHERE project_id=$1 AND id<>$2 AND rank > $3",
                    project_id, item_id, ref["rank"])
                new_rank = (ref["rank"] + float(nxt)) / 2 if nxt is not None else ref["rank"] + RANK_STEP
            row = await conn.fetchrow(
                "UPDATE backlog_items SET rank=$3, version=version+1, updated_at=now() "
                "WHERE project_id=$1 AND id=$2 RETURNING *", project_id, item_id, new_rank)
            # too close to a neighbour → renumber everything with even spacing (order preserved)
            tight = await conn.fetchval(
                "SELECT COUNT(*) FROM (SELECT rank - LAG(rank) OVER (ORDER BY rank) AS gap FROM backlog_items "
                "WHERE project_id=$1) g WHERE gap IS NOT NULL AND gap < 0.001", project_id)
            if tight:
                await conn.execute(
                    "UPDATE backlog_items b SET rank = r.n * $2 FROM (SELECT id, ROW_NUMBER() OVER "
                    "(ORDER BY rank, created_at) AS n FROM backlog_items WHERE project_id=$1) r WHERE b.id=r.id",
                    project_id, RANK_STEP)
                row = await conn.fetchrow("SELECT * FROM backlog_items WHERE id=$1", item_id)
            return row

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

    async def detach_items_from_iteration(self, iteration_id: str) -> int:
        """Cancelled sprint: committed-but-unstarted items go back to ready; nothing stays attached."""
        assert self.pool
        r = await self.pool.execute(
            "UPDATE backlog_items SET iteration_id=NULL, version=version+1, updated_at=now(), "
            "status = CASE WHEN status IN ('in_sprint','in_progress') THEN 'ready' ELSE status END "
            "WHERE iteration_id=$1", iteration_id)
        return int(r.split()[-1])

    async def add_item_to_sprint(
        self, project_id: str, item_id: str, iteration_id: str, *, force: bool,
    ) -> tuple[str, float, float]:
        """Capacity check + assignment in ONE transaction (the sprint row is locked), so two concurrent adds cannot
        both squeeze past the capacity. Returns (outcome, points_after, capacity); outcome is 'added', 'over'
        (needs force), 'not_ready' or 'closed'."""
        assert self.pool
        async with self.pool.acquire() as conn, conn.transaction():
            it = await conn.fetchrow("SELECT status, capacity FROM iterations WHERE id=$1 FOR UPDATE", iteration_id)
            if it is None or it["status"] not in ("planned", "active"):
                return "closed", 0.0, 0.0
            item = await conn.fetchrow(
                "SELECT status, estimate, iteration_id FROM backlog_items WHERE project_id=$1 AND id=$2 FOR UPDATE",
                project_id, item_id)
            cap = float(it["capacity"])
            if item is None or item["status"] != "ready" or item["iteration_id"] is not None or item["estimate"] is None:
                return "not_ready", 0.0, cap
            current = float(await conn.fetchval(
                "SELECT COALESCE(SUM(estimate),0) FROM backlog_items WHERE iteration_id=$1", iteration_id))
            after = current + float(item["estimate"])
            if after > cap and not force:
                return "over", after, cap
            await conn.execute(
                "UPDATE backlog_items SET status='in_sprint', iteration_id=$2, version=version+1, updated_at=now() "
                "WHERE id=$1", item_id, iteration_id)
            return ("added" if after <= cap else "added_over"), after, cap

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

    # ------------------------------------------------------------------ applying proposals (atomic, once)
    @staticmethod
    async def _claim_proposal(conn: asyncpg.Connection, proposal_id: str, actor_id: str | None) -> bool:
        got = await conn.fetchval(
            "UPDATE agile_proposals SET status='applied', decided_at=now(), decided_by=$2 "
            "WHERE id=$1 AND status='proposed' RETURNING id", proposal_id, actor_id)
        return got is not None

    async def apply_refine(
        self, *, project_id: str, proposal_id: str, ops: list[dict[str, Any]], actor_id: str | None,
    ) -> dict[str, Any] | None:
        """Apply a refinement proposal in ONE transaction. Returns None when it was already decided
        (so a replay or a race can never apply it twice)."""
        from ..agile.rules import COMMITTED

        assert self.pool
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"rank:{project_id}")
            if not await self._claim_proposal(conn, proposal_id, actor_id):
                return None
            out: dict[str, Any] = {"created": [], "updated": [], "dropped": [], "skipped": []}
            ref_ids: dict[str, str] = {}

            async def epic_id(ref: str | None) -> str | None:
                if not ref:
                    return None
                if ref in ref_ids:
                    return ref_ids[ref]
                return await conn.fetchval(
                    "SELECT id FROM backlog_items WHERE project_id=$1 AND item_key=$2 AND type='epic'", project_id, ref)

            for op in ops:
                if op["op"] == "create":
                    f: dict[str, Any] = {
                        "type": op["type"], "title": op["title"], "description": op["description"],
                        "acceptance_criteria": op["acceptanceCriteria"], "estimate": op["estimate"],
                        "components": op["components"], "epic_id": await epic_id(op.get("epic")),
                    }
                    # 'refined' = has real content; work items without acceptance criteria stay 'new'.
                    f["status"] = "refined" if op["type"] == "epic" or op["acceptanceCriteria"] else "new"
                    row = await self._insert_item(conn, project_id, actor_id, f)
                    ref_ids[op["ref"]] = row["id"]
                    out["created"].append(row["item_key"])
                    continue
                item = await conn.fetchrow(
                    "SELECT * FROM backlog_items WHERE project_id=$1 AND item_key=$2 FOR UPDATE", project_id, op["target"])
                if item is None or item["status"] in COMMITTED or item["status"] == "dropped":
                    out["skipped"].append({"ref": op["ref"], "reason": "item changed since the proposal"})
                    continue
                if op["op"] == "drop":
                    if item["status"] in ("new", "refined", "ready"):
                        await conn.execute(
                            "UPDATE backlog_items SET status='dropped', version=version+1, updated_at=now() WHERE id=$1",
                            item["id"])
                        out["dropped"].append(item["item_key"])
                    else:
                        out["skipped"].append({"ref": op["ref"], "reason": f"item is {item['status']}"})
                    continue
                sets: dict[str, Any] = {}
                if op.get("title"):
                    sets["title"] = op["title"]
                if op.get("description"):
                    sets["description"] = op["description"]
                if op.get("acceptanceCriteria"):
                    sets["acceptance_criteria"] = op["acceptanceCriteria"]
                if op.get("estimate") is not None and item["type"] != "epic":
                    sets["estimate"] = op["estimate"]
                if op.get("components"):
                    sets["components"] = op["components"]
                if op.get("epic"):
                    sets["epic_id"] = await epic_id(op["epic"])
                if not sets:
                    out["skipped"].append({"ref": op["ref"], "reason": "nothing to change"})
                    continue
                cols = ", ".join(f"{k}=${i + 2}" for i, k in enumerate(sets))
                await conn.execute(
                    f"UPDATE backlog_items SET {cols}, version=version+1, updated_at=now() WHERE id=$1",
                    item["id"], *sets.values())
                out["updated"].append(item["item_key"])
            return out

    async def apply_plan(
        self, *, project_id: str, proposal_id: str, iteration_id: str, keys: list[str], goal: str,
        actor_id: str | None,
    ) -> dict[str, Any] | None:
        """Commit the planned items to the sprint atomically; items that stopped being 'ready' are skipped."""
        assert self.pool
        async with self.pool.acquire() as conn, conn.transaction():
            status = await conn.fetchval("SELECT status FROM iterations WHERE id=$1 FOR UPDATE", iteration_id)
            if status not in ("planned", "active"):
                # A settled (closed/cancelled) sprint must never receive work, e.g. when its Plan stage is re-run.
                await conn.execute("UPDATE agile_proposals SET status='superseded', decided_at=now() "
                                   "WHERE id=$1 AND status='proposed'", proposal_id)
                return {"assigned": [], "skipped": sorted(set(keys)), "reason": f"the sprint is {status or 'missing'}"}
            if not await self._claim_proposal(conn, proposal_id, actor_id):
                return None
            rows = await conn.fetch(
                "UPDATE backlog_items SET status='in_sprint', iteration_id=$3, version=version+1, updated_at=now() "
                "WHERE project_id=$1 AND item_key = ANY($2) AND status='ready' AND iteration_id IS NULL "
                "RETURNING item_key", project_id, keys, iteration_id)
            assigned = sorted(r["item_key"] for r in rows)
            if goal:
                await conn.execute("UPDATE iterations SET goal=$2 WHERE id=$1 AND goal=''", iteration_id, goal)
            return {"assigned": assigned, "skipped": sorted(set(keys) - set(assigned))}

    async def list_agile_project_ids(self, *, any_project: bool = False) -> list[str]:
        """Projects that use an Agile methodology AND have a Jira project key (the scheduled sync set), or with
        `any_project=True` every Agile project (the reconcile set)."""
        assert self.pool
        if any_project:
            return [r["project_id"] for r in await self.pool.fetch("SELECT project_id FROM project_agile ORDER BY project_id")]
        rows = await self.pool.fetch(
            "SELECT a.project_id FROM project_agile a JOIN projects p ON p.id=a.project_id "
            "WHERE COALESCE(p.jira_project_key,'') <> '' ORDER BY a.project_id")
        return [r["project_id"] for r in rows]
