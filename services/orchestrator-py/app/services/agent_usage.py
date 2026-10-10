"""What custom agents spend, and the ceilings that stop them.

Two ceilings: an agent's own token cap per run (in its definition, enforced by the runtime) and a project's monthly total (set by the
project manager, enforced here before a run starts). Tokens are counted, not money: the price depends on the model the administrator routes to.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from ..domain.errors import SdlcError

log = logging.getLogger("agent_usage")


def month_start(now: datetime | None = None) -> datetime:
    n = now or datetime.now(UTC)
    return n.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class AgentUsage:
    def __init__(self, repo: Any) -> None:
        self._repo = repo

    async def month_used(self, project_id: str) -> int:
        return sum(r["prompt"] + r["completion"] for r in await self._repo.usage_for_project(project_id, month_start()))

    async def check(self, project_id: str | None) -> None:
        """Refuse to start a run when the project has spent its monthly budget."""
        if not project_id:
            return
        limit = await self._repo.get_limit(project_id)
        if limit is None:
            return
        used = await self.month_used(project_id)
        if used >= limit:
            raise SdlcError("VALIDATION_FAILED", f"This project has used its monthly budget for custom agents ({used:,} of {limit:,} tokens). A project manager can raise it under Agents and skills -> Usage")

    async def record(self, res: Any, project_id: str | None, source: str) -> None:
        """One row per agent in the tree that ran. Never lets a bookkeeping failure fail the run."""
        rows: list[dict[str, Any]] = []

        def walk(r: Any) -> None:
            rows.append({"def_id": r.agent_id, "project_id": project_id, "source": source, "prompt": r.usage["promptTokens"], "completion": r.usage["completionTokens"]})
            for c in r.children:
                walk(c)
        walk(res)
        try:
            await self._repo.record_usage(rows)
        except Exception:  # noqa: BLE001
            log.warning("could not record custom agent usage", exc_info=True)
