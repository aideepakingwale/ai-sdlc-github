"""Deterministic model-tier selection. The planner uses this to route
each task to the right capacity — non-LLM for trivial/mechanical work, a local
lightweight model for light generation, the frontier chain for heavy reasoning.
No LLM call is made to decide; the policy is a pure function of the task shape,
so routing is reproducible and auditable."""

from __future__ import annotations

from typing import Literal

Tier = Literal["non_llm", "local", "frontier"]

# Tasks that are mechanical string/format/validate work — no model needed.
_NON_LLM_HINTS = (
    "count", "format", "validate", "lint", "checksum", "uuid", "slugify",
    "sort", "dedupe", "word count", "syntax check",
)
# Heavy reasoning / design / architecture / code generation → frontier.
_FRONTIER_HINTS = (
    "architecture", "design", "hld", "lld", "trade-off", "tradeoff", "adr",
    "generate code", "implement", "refactor", "algorithm", "threat model",
    "test strategy", "openapi", "schema design",
)


def classify_tier(
    text: str,
    *,
    has_tools: bool = False,
    context_tokens: int = 0,
    local_window: int = 8_000,
) -> Tier:
    """Pick the cheapest tier that fits the task.

    - non_llm  when the ask is a mechanical/deterministic operation
    - frontier when it is heavy reasoning/design, needs tools, or exceeds the
      local model's context window
    - local    otherwise (short, light generation)
    """
    lowered = text.lower()
    if any(h in lowered for h in _NON_LLM_HINTS) and len(text) < 400:
        return "non_llm"
    if has_tools or context_tokens > local_window:
        return "frontier"
    if any(h in lowered for h in _FRONTIER_HINTS):
        return "frontier"
    if len(text) <= 600:
        return "local"
    return "frontier"


def tier_to_request(tier: Tier) -> str:
    """Map a policy tier to the ai-client request tier (non_llm never calls it)."""
    return "local" if tier == "local" else "frontier"
