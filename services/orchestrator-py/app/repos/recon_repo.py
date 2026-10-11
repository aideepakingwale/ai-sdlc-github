"""Postgres access for the Bedrock reconciliation (migration 0052) and the ledger rows it compares. Services never write SQL elsewhere."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import asyncpg

RUN_COLS = ("id", "day", "source", "triggered_by", "started_at", "finished_at", "status", "threshold", "convention", "ledger_tokens", "aws_tokens", "drift",
            "matched", "mismatched", "missing_in_ledger", "missing_in_aws", "unmatchable_ledger", "estimated_pairs", "by_model", "note", "error")


class ReconRepo:
    def __init__(self, db: Any) -> None:
        self._db = db

    @property
    def _p(self) -> asyncpg.Pool:
        assert self._db.pool
        return self._db.pool

    async def bedrock_ledger(self, since: datetime, until: datetime) -> list[dict[str, Any]]:
        rows = await self._p.fetch(
            """SELECT provider_request_id, model, ts, input_tokens, cache_read_tokens, cache_write_tokens, output_tokens, estimated, project_id, activity, status
               FROM llm_usage_events WHERE provider = 'bedrock' AND ts >= $1 AND ts < $2""", since, until)
        return [dict(r) for r in rows]

    async def needs_run(self, day: date, retry_after_hours: int = 6) -> bool:
        """No result yet, or the last attempt found no log / failed more than `retry_after_hours` ago (logs can arrive late; credentials get fixed)."""
        return not await self._p.fetchval(
            """SELECT 1 FROM usage_reconciliation_runs WHERE day=$1
               AND (status IN ('ok','drift') OR finished_at > now() - make_interval(hours => $2))""", day, retry_after_hours)

    async def save_run(self, run: dict[str, Any], diffs: list[dict[str, Any]]) -> None:
        async with self._p.acquire() as conn, conn.transaction():
            await conn.execute("DELETE FROM usage_reconciliation_runs WHERE day=$1", run["day"])      # a re-run replaces the day (diffs cascade)
            vals = {**{c: None for c in RUN_COLS}, "by_model": [], "finished_at": datetime.now(run["started_at"].tzinfo), **run}
            cols = [c for c in RUN_COLS if vals[c] is not None]
            await conn.execute(f"INSERT INTO usage_reconciliation_runs ({','.join(cols)}) VALUES ({','.join(f'${i + 1}' for i in range(len(cols)))})", *[vals[c] for c in cols])
            if diffs:
                await conn.executemany(
                    """INSERT INTO usage_reconciliation_diffs (run_id, kind, provider_request_id, model, ts, ledger_tokens, aws_tokens, delta, project_id, activity)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
                    [(run["id"], d["kind"], d["provider_request_id"], d["model"], d["ts"], d["ledger_tokens"], d["aws_tokens"], d["delta"], d["project_id"], d["activity"]) for d in diffs])

    async def runs(self, limit: int) -> list[dict[str, Any]]:
        return [dict(r) for r in await self._p.fetch("SELECT * FROM usage_reconciliation_runs ORDER BY day DESC LIMIT $1", limit)]

    async def run_for(self, day: date) -> dict[str, Any] | None:
        r = await self._p.fetchrow("SELECT * FROM usage_reconciliation_runs WHERE day=$1", day)
        return dict(r) if r else None

    async def diffs(self, run_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in await self._p.fetch(
            "SELECT kind, provider_request_id, model, ts, ledger_tokens, aws_tokens, delta, project_id, activity FROM usage_reconciliation_diffs WHERE run_id=$1 ORDER BY abs(delta) DESC", run_id)]
