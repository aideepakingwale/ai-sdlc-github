"""Storage for failed-call captures (`llm_failure_captures`, migration 0056). Reads are always scoped to a project."""
from __future__ import annotations

import json
from typing import Any

import asyncpg


class CaptureRepo:
    def __init__(self, db: Any) -> None:
        self._db = db

    @property
    def _p(self) -> asyncpg.Pool:
        assert self._db.pool
        return self._db.pool

    async def save(self, row: dict[str, Any]) -> None:
        """One row per request. A second failure of the same request (the model answered, then the application rejected it)
        replaces the reason and the response but keeps the earliest attempts and error if the new row has none."""
        await self._p.execute(
            """INSERT INTO llm_failure_captures (request_id, project_id, user_id, activity, stage, tag, reason, error, attempts,
                                                 request_body, response_body, request_cut, response_cut)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
               ON CONFLICT (request_id) DO UPDATE SET
                 reason = EXCLUDED.reason,
                 error = COALESCE(EXCLUDED.error, llm_failure_captures.error),
                 attempts = CASE WHEN jsonb_array_length(EXCLUDED.attempts) > 0 THEN EXCLUDED.attempts ELSE llm_failure_captures.attempts END,
                 request_body = COALESCE(EXCLUDED.request_body, llm_failure_captures.request_body),
                 response_body = COALESCE(EXCLUDED.response_body, llm_failure_captures.response_body),
                 request_cut = EXCLUDED.request_cut OR llm_failure_captures.request_cut,
                 response_cut = EXCLUDED.response_cut OR llm_failure_captures.response_cut""",
            row["request_id"], row.get("project_id"), row.get("user_id"), row.get("activity"), row.get("stage"), row.get("tag"),
            row["reason"], row.get("error"), row.get("attempts") or [],   # the pool's jsonb codec encodes it
             row.get("request_body"), row.get("response_body"),
            bool(row.get("request_cut")), bool(row.get("response_cut")))

    async def get(self, project_id: str, request_id: str) -> dict[str, Any] | None:
        r = await self._p.fetchrow("SELECT * FROM llm_failure_captures WHERE request_id=$1 AND project_id=$2", request_id, project_id)
        if not r:
            return None
        out = dict(r)
        if isinstance(out.get("attempts"), str):
            out["attempts"] = json.loads(out["attempts"])
        return out

    async def purge(self, days: int) -> int:
        """Delete captures older than `days` days (0 deletes every capture)."""
        r = await self._p.execute("DELETE FROM llm_failure_captures WHERE created_at < now() - make_interval(days => $1)", max(0, int(days)))
        return int(r.split()[-1])
