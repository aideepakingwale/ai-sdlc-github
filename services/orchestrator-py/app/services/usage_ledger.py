"""The token ledger writer (docs/token-governance.md).

`LlmClient` is the only caller. It hands over the gateway's per-attempt usage log and this turns it
into one `llm_usage_events` row per provider attempt, stamped with who/where/what from the usage
context. A write failure is logged loudly but never fails the generation: in this phase the
ledger is for measurement; the quota gate (a later phase) reads balances, not this write path.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from . import usage_context as uc

log = logging.getLogger("usage-ledger")

_STATUSES = {"ok", "error", "canceled", "refused", "truncated"}


def _n(v: Any) -> int:
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


def synthesize_log(data: dict[str, Any]) -> list[dict[str, Any]]:
    """A gateway that predates the usage log still reports the answering call: make one record from it."""
    u = data.get("usage") or {}
    return [{
        "provider": data.get("provider") or "unknown", "model": data.get("model") or "", "outcome": "ok",
        "usage": u, "latencyMs": 0,
    }]


def build_rows(
    *, request_id: str, usage_log: list[dict[str, Any]], tag: str | None, activity: str | None,
    latency_ms: int = 0, truncated: bool = False,
) -> list[dict[str, Any]]:
    ctx = uc.current()
    act, known = uc.normalise_activity(activity)
    if not known:
        log.error("LLM call with unknown activity %r (tag=%s); recorded as 'other'", activity, tag)
    if ctx.project_id is None and any((a.get("provider") or "") not in uc.NON_BILLABLE_PROVIDERS for a in usage_log):
        # A project is required to work, so every billable call has one: this is a bug in the entry point (the target is zero).
        log.warning("UNATTRIBUTED model call: no project in the usage context (activity=%s tag=%s user=%s)", act, tag, ctx.user_id)
    last_ok = max((i for i, a in enumerate(usage_log) if a.get("outcome") == "ok"), default=-1)
    rows: list[dict[str, Any]] = []
    for i, a in enumerate(usage_log):
        u = a.get("usage") or {}
        # `input_tokens` is the uncached part. A gateway that only reports promptTokens/completionTokens
        # (older, or a provider without a split) has no split: the whole prompt is uncached input.
        cache_read, cache_write = _n(u.get("cacheReadTokens")), _n(u.get("cacheWriteTokens"))
        if "inputTokens" in u:
            inp = _n(u.get("inputTokens"))
        else:
            inp = max(0, _n(u.get("promptTokens")) - cache_read - cache_write)
        outcome = a.get("outcome") or "error"
        status = "truncated" if (i == last_ok and truncated and outcome == "ok") else outcome
        provider = str(a.get("provider") or "unknown")
        rows.append({
            "id": str(uuid.uuid4()), "request_id": request_id, "attempt_no": i + 1,
            "provider": provider, "model": str(a.get("model") or ""), "region": a.get("region"),
            "provider_request_id": a.get("providerRequestId"),
            "project_id": ctx.project_id, "stage": ctx.stage, "user_id": ctx.user_id, "actor_type": ctx.actor_type,
            "activity": act, "tag": (tag or "")[:200] or None, "agent_id": ctx.agent_id, "run_id": ctx.run_id,
            "input_tokens": inp, "cache_read_tokens": cache_read, "cache_write_tokens": cache_write,
            "output_tokens": _n(u.get("completionTokens")),
            "status": status if status in _STATUSES else "error",
            "error_class": a.get("errorKind"), "stop_reason": a.get("stopReason"),
            "latency_ms": _n(a.get("latencyMs")) or (latency_ms if len(usage_log) == 1 else 0),
            "estimated": bool(u.get("estimated")),
            "billable": provider not in uc.NON_BILLABLE_PROVIDERS,
        })
    return rows


class UsageLedger:
    def __init__(self, db: Any) -> None:
        self._db = db

    async def record(self, **kw: Any) -> list[dict[str, Any]]:
        """Write the rows for one logical call; returns them (the caller can total them)."""
        rows = build_rows(**kw)
        if not rows:
            return rows
        try:
            await self._db.insert_usage_events(rows)
        except Exception:  # noqa: BLE001 — measurement must not take generation down, but never fail silently
            log.error("usage ledger write FAILED for request %s (%d attempts, %d tokens)", kw.get("request_id"),
                      len(rows), sum(r["input_tokens"] + r["cache_read_tokens"] + r["cache_write_tokens"] + r["output_tokens"] for r in rows),
                      exc_info=True)
        return rows
