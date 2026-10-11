"""What was asked and what came back, for LLM calls that went wrong (docs/token-governance.md, section 20).

The token ledger says THAT a request wasted tokens; this keeps WHY: the messages sent, the model's answer (or the gateway's
error), and the per-attempt errors. Only failures are kept, for a limited time, and only a project's manager and administrators
can read them (services/usage_reports.py), because a prompt can contain the project's documents.

Never raises: losing a capture must not fail the call that was being diagnosed.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from . import usage_context

log = logging.getLogger("failure-capture")

REASONS = ("error", "unreachable", "refused", "canceled", "truncated", "failed_over", "invalid_output")
_ATTEMPT_KEYS = ("provider", "model", "outcome", "errorKind", "error", "providerRequestId", "stopReason", "latencyMs")


def failure_reason(usage_log: list[dict[str, Any]], truncated: bool = False) -> str | None:
    """Why a request that reached the gateway counts as a failure, or None when it was clean."""
    if truncated:
        return "truncated"
    bad = [a for a in usage_log if a.get("outcome") not in (None, "ok")]
    if not bad:
        return None
    if usage_log and usage_log[-1].get("outcome") == "ok":
        return "failed_over"                      # a later attempt answered
    out = str(bad[-1].get("outcome"))
    return out if out in REASONS else "error"


def attempts_of(usage_log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The per-attempt story without token counts (the ledger holds those): who was tried, what failed and why."""
    return [{k: a.get(k) for k in _ATTEMPT_KEYS if a.get(k) is not None} for a in usage_log]


class FailureCapture:
    def __init__(self, repo: Any, *, enabled: bool = True, days: int = 14, max_chars: int = 100_000) -> None:
        self._repo = repo
        self.enabled = bool(enabled) and days > 0
        self.days = max(0, int(days))
        self._max = max(1_000, int(max_chars))
        self._task: asyncio.Task[None] | None = None

    def _cap(self, s: str | None) -> tuple[str | None, bool]:
        if s is None:
            return None, False
        return (s, False) if len(s) <= self._max else (s[: self._max], True)

    async def record(self, *, request_id: str | None, request_body: str | None, reason: str, error: str | None = None,
                     response: str | None = None, attempts: list[dict[str, Any]] | None = None, activity: str | None = None,
                     tag: str | None = None) -> None:
        if not self.enabled or not request_id:
            return
        ctx = usage_context.current()
        req, req_cut = self._cap(request_body)
        resp, resp_cut = self._cap(response)
        try:
            await self._repo.save({
                "request_id": request_id, "project_id": ctx.project_id, "user_id": ctx.user_id, "activity": usage_context.normalise_activity(activity)[0],
                "stage": ctx.stage, "tag": tag, "reason": reason if reason in REASONS else "error",
                "error": (error or "")[:500] or None, "attempts": attempts or [], "request_body": req, "response_body": resp,
                "request_cut": req_cut, "response_cut": resp_cut,
            })
        except Exception:  # noqa: BLE001 - a diagnostic aid must never break the call
            log.warning("could not store a failure capture", exc_info=True)

    async def purge(self) -> int:
        return await self._repo.purge(self.days)

    # ------------------------------------------------------------------ retention
    def start(self) -> None:
        if self._task is None and self.enabled:
            self._task = asyncio.get_running_loop().create_task(self._loop(), name="failure-capture-retention")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                n = await self.purge()
                if n:
                    log.info("deleted %d failure capture(s) older than %d days", n, self.days)
            except Exception:  # noqa: BLE001
                log.error("failure capture retention failed", exc_info=True)
            await asyncio.sleep(3600)
