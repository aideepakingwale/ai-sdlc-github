"""What custom agents spend.

An agent's own token cap per run (in its definition, enforced by the runtime) is a per-run safety ceiling. The project's monthly budget for
custom agents that used to live here is retired (docs/token-governance.md, phase 4): agents draw from the project's token balance like every
other piece of work, and the token gate refuses new runs when that balance is used up. Tokens are counted, not money.
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
