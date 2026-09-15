"""AI observability: every LLM call and MCP tool execution is traced.

Design:
  - A contextvar carries the run context (project, stage slot) so the
    instrumented LlmClient / tool wrapper can attribute spans without
    threading identifiers through every call signature.
  - Traces persist to Postgres (`llm_traces`) — one row per span with tokens,
    latency, status and an estimated cost — powering the admin dashboard
    (`/api/observability/*`), the Prometheus text endpoint (`/metrics`) and
    any external BI over the same table.
  - Recording is fire-and-safe: telemetry failures NEVER break the pipeline.
  - LangSmith is the optional deep-trace tier: the LangGraph pipeline emits
    full run trees when LANGCHAIN_TRACING_V2/LANGSMITH_API_KEY are set — no
    code path here depends on it.
"""

from __future__ import annotations

import logging
import time
from contextvars import ContextVar
from typing import Any

log = logging.getLogger("telemetry")

# Rough public list prices per 1M tokens (input, output) for cost ESTIMATES on
# the dashboard — indicative, not billing. Unknown providers count as 0.
COST_PER_MTOK: dict[str, tuple[float, float]] = {
    "bedrock": (5.00, 25.00),   # anthropic.claude-opus-4-8
    "groq": (0.59, 0.79),       # llama-3.3-70b
    "gemini": (0.10, 0.40),     # gemini flash
    "grok": (2.00, 10.00),
    "local": (0.0, 0.0),
    "mock": (0.0, 0.0),
}

_run_context: ContextVar[dict[str, Any] | None] = ContextVar("obs_run_context", default=None)


def set_run_context(project_id: str | None, stage: int | None = None) -> None:
    _run_context.set({"projectId": project_id, "stage": stage})


def estimate_cost_usd(provider: str | None, prompt_tokens: int, completion_tokens: int) -> float:
    cin, cout = COST_PER_MTOK.get(provider or "", (0.0, 0.0))
    return round((prompt_tokens * cin + completion_tokens * cout) / 1_000_000, 6)


class TelemetryService:
    def __init__(self, db: Any) -> None:
        self._db = db

    async def record(
        self, *, kind: str, provider: str | None = None, model: str | None = None,
        tier: str | None = None, tag: str | None = None,
        prompt_tokens: int = 0, completion_tokens: int = 0,
        latency_ms: int = 0, status: str = "ok", error: str | None = None,
    ) -> None:
        ctx = _run_context.get() or {}
        try:
            await self._db.insert_trace(
                project_id=ctx.get("projectId"), stage=ctx.get("stage"), kind=kind,
                provider=provider, model=model, tier=tier, tag=tag,
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                latency_ms=latency_ms, status=status, error=(error or "")[:500] or None,
                cost_usd=estimate_cost_usd(provider, prompt_tokens, completion_tokens),
            )
        except Exception:  # observability must never take the pipeline down
            log.warning("trace insert failed", exc_info=True)

    async def summary(self, days: int = 7) -> dict[str, Any]:
        return await self._db.obs_summary(days)

    async def traces(self, limit: int = 100, project_id: str | None = None) -> list[dict[str, Any]]:
        return await self._db.obs_recent(limit, project_id)


class Stopwatch:
    """Tiny latency helper: `with Stopwatch() as sw: ...; sw.ms`."""

    def __enter__(self) -> "Stopwatch":
        self._t0 = time.perf_counter()
        self.ms = 0
        return self

    def __exit__(self, *exc: object) -> None:
        self.ms = int((time.perf_counter() - self._t0) * 1000)
