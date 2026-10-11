"""The token gate (docs/token-governance.md, phase 4): new work does not start while a pool is at zero or below.

The rule, as decided:
  * the quota is a balance and may go negative: the activity in flight finishes and takes what it needs;
  * at zero or below, NEW tasks are refused (reading, reports, approvals of finished work and asking for tokens stay available);
  * if the balance cannot be read, nothing starts (fail closed);
  * a task is admitted once, at its start; calls inside an admitted task are not re-checked, but one task has a safety ceiling.

Two places call the gate. The entry points (HTTP routes that start work, the job queue) admit a task up front, so the person gets a
clear refusal before anything happens and a fan-out of parallel calls is admitted as one task. And `LlmClient` calls `before_call`
for every model call: a call from a task nobody admitted is admitted (or refused) on its first call, so no entry point can be forgotten.
A pool that was never allocated is unlimited and never blocks. Work with no project is charged to the organisation pool.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from ..domain.errors import SdlcError
from ..repos.pool_repo import PoolRepo
from . import usage_context as uc

log = logging.getLogger("token-gate")


def _fmt(n: int) -> str:
    a = abs(n)
    s = f"{a / 1_000_000:.1f}M" if a >= 1_000_000 else f"{round(a / 1000)}k" if a >= 10_000 else f"{a:,}"
    return f"-{s}" if n < 0 else s


MODES = ("enforce", "observe", "off")
MODE_SETTING = "token_gate_mode"
_MODE_TTL = 5.0


class TokenGate:
    """`mode` is the default from the environment; an administrator can change it at runtime (a platform setting, read with a short cache), so a
    deployment can run in `observe` first and move to `enforce` without a restart."""

    def __init__(self, repo: PoolRepo, *, mode: str = "enforce", task_ceiling: int = 0, mode_reader: Callable[[], Awaitable[str | None]] | None = None) -> None:
        self._repo, self._default, self._ceiling = repo, mode if mode in MODES else "enforce", max(0, int(task_ceiling))
        self._reader = mode_reader
        self._cached: tuple[str, float] | None = None

    @property
    def default_mode(self) -> str:
        return self._default

    async def mode(self) -> str:
        """The mode in force now: the administrator's setting, else the environment default. If the setting cannot be read the last known
        value stays (the gate never becomes looser because a read failed)."""
        if self._reader is None:
            return self._default
        now = time.monotonic()
        if self._cached and now - self._cached[1] < _MODE_TTL:
            return self._cached[0]
        try:
            raw = await self._reader()
            value = raw if raw in MODES else self._default
        except Exception:  # noqa: BLE001
            value = self._cached[0] if self._cached else self._default
        self._cached = (value, now)
        return value

    def forget_mode(self) -> None:
        self._cached = None

    # ------------------------------------------------------------------ admission
    async def admit(self, *, project_id: str | None, user_id: str | None, entry: str) -> None:
        """Check the balance at the start of a task and mark the current context admitted. Raises TOKENS_EXHAUSTED (or TOKENS_UNAVAILABLE)."""
        mode = await self.mode()
        if mode == "off":
            uc.update(admitted=True)
            return
        try:
            state = await self._repo.gate_state(project_id)
        except Exception as err:  # noqa: BLE001 — fail closed: a gate that cannot look must not wave work through
            log.error("token gate could not read the balance (entry=%s): %s", entry, err, exc_info=True)
            raise SdlcError("TOKENS_UNAVAILABLE", "The token balance could not be checked just now, so new work is paused. Try again in a moment.") from err
        proj, org = state["project"], state["org"]
        blocked: tuple[str, dict[str, Any]] | None = None
        if proj and proj["metered"] and proj["balance_tokens"] <= 0:
            blocked = ("project", proj)
        elif org and org["metered"] and org["balance_tokens"] <= 0:
            blocked = ("organisation", org)
        if blocked:
            pool, row = blocked
            enforced = mode == "enforce"
            try:
                await self._repo.record_block(project_id=project_id, user_id=user_id, pool=pool, balance=row["balance_tokens"], entry=entry, enforced=enforced)
            except Exception:  # noqa: BLE001 — the refusal does not depend on the log
                log.warning("could not record a token gate block", exc_info=True)
            if enforced:
                raise self._refusal(pool, row["balance_tokens"], project_id)
            log.warning("TOKEN GATE (observe): would refuse %s: %s pool at %d", entry, pool, row["balance_tokens"])
        metered = bool((proj and proj["metered"]) or (org and org["metered"]))
        uc.update(admitted=True, task=uc.TaskBudget(self._ceiling) if self._ceiling and metered else None)

    async def admit_started(self, project_id: str | None) -> None:
        """A task that was admitted earlier (a queued stage run) is starting: mark it admitted and give it its ceiling. Never refuses."""
        budget = None
        if await self.mode() != "off" and self._ceiling:
            try:
                state = await self._repo.gate_state(project_id)
                if any(x and x["metered"] for x in state.values()):
                    budget = uc.TaskBudget(self._ceiling)
            except Exception:  # noqa: BLE001 — the run was already admitted; no ceiling is better than no run
                log.warning("token gate could not read the balance for a started run", exc_info=True)
        uc.update(admitted=True, task=budget)

    @staticmethod
    def _refusal(pool: str, balance: int, project_id: str | None) -> SdlcError:
        who = "This project has" if pool == "project" else "The organisation has"
        return SdlcError(
            "TOKENS_EXHAUSTED",
            f"{who} used all of its tokens ({_fmt(balance)} left), so new work can't start. An administrator can allocate more; you can ask for more tokens.",
            {"pool": pool, "balance": balance, "projectId": project_id, "canRequest": bool(project_id)},
        )

    # ------------------------------------------------------------------ per model call
    async def before_call(self, activity: str) -> None:
        """Called by LlmClient before every model call. An admitted task goes on (until its ceiling); anything else is admitted now or refused."""
        ctx = uc.current()
        if ctx.admitted:
            t = ctx.task
            if t is not None and t.ceiling and t.spent >= t.ceiling and await self.mode() == "enforce":
                raise SdlcError(
                    "TASK_TOKEN_CEILING",
                    f"This task used its safety limit of {_fmt(t.ceiling)} tokens and was stopped. What it finished is kept. Start it again to continue, or ask an administrator to look at why it used so much.",
                    {"ceiling": t.ceiling, "spent": t.spent})
            return
        await self.admit(project_id=ctx.project_id, user_id=ctx.user_id, entry=f"call:{activity}")

    def note_spend(self, rows: list[dict[str, Any]]) -> None:
        """Add what a model call just used (billable tokens) to the current task's running total."""
        t = uc.current().task
        if t is None:
            return
        t.spent += sum(int(r["input_tokens"]) + int(r["cache_read_tokens"]) + int(r["cache_write_tokens"]) + int(r["output_tokens"]) for r in rows if r.get("billable", True))
