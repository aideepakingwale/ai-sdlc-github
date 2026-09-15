"""Deterministic, zero-token planning for the multi-model Plan Review.

Everything here is a pure function of the gateway roster + the stage template +
the writer's saved overrides — no LLM call. This is what makes building,
previewing, editing and regenerating a plan cost nothing; tokens are spent only
when a writer triggers execution, and per-step model routing right-sizes that
spend (mechanical steps use no model at all).
"""

from __future__ import annotations

from typing import Any

# Tier hint per provider for the picker; the gateway routes by tier, so this
# labels capacity class rather than a hard capability.
_TIER_HINT = {"bedrock": "frontier", "groq": "frontier", "grok": "frontier",
              "gemini": "frontier", "local": "local", "mock": "mock"}
# Catalog display order → the first healthy one is flagged "recommended".
_MODEL_ORDER = ["bedrock", "groq", "gemini", "grok", "local"]
# Auto-routing preference per tier: frontier leads with Bedrock; light work picks
# the cheapest capable model first.
_FRONTIER_ORDER = ["bedrock", "groq", "gemini", "grok"]
_LIGHT_ORDER = ["local", "gemini", "groq", "grok", "bedrock"]

_RATIONALE = {
    "frontier": "Heavy design / code reasoning with tool use → frontier tier.",
    "local": "Light, well-scoped task → a cost-efficient model is sufficient.",
    "non_llm": "Deterministic/mechanical step → no model needed.",
}


def build_model_catalog(roster: dict) -> dict:
    """Normalize the gateway provider roster into the selectable model catalog:
    one entry per configured provider (id = 'provider/model'), with tier hint,
    vision support, health, and the first healthy one flagged recommended."""
    provs = {p["provider"]: p for p in roster.get("providers", [])}
    models: list[dict] = []
    recommended: str | None = None
    for pid in _MODEL_ORDER:
        p = provs.get(pid)
        if not p or not p.get("model"):
            continue
        configured = bool(p.get("configured"))
        healthy = configured and p.get("breaker") == "closed"
        entry = {
            "id": f"{pid}/{p['model']}", "provider": pid, "model": p["model"],
            "tier": _TIER_HINT.get(pid, "frontier"), "vision": bool(p.get("vision")),
            "configured": configured, "healthy": healthy,
            "reason": None if healthy else ("not configured" if not configured else f"breaker {p.get('breaker')}"),
        }
        models.append(entry)
        if recommended is None and healthy:
            recommended = entry["id"]
    return {
        "mode": roster.get("mode", "auto"),
        "effectiveMock": bool(roster.get("effectiveMock", True)),
        "recommended": recommended,
        "models": models,
    }


def resolve_step_model(tier: str, roster: dict, override: str | None = None) -> dict:
    """Resolve the concrete model an LLM step will use — deterministic.

    A valid, healthy `override` ('provider/model' or a bare model id) wins and is
    marked source='override'. Otherwise the cheapest capable healthy model for
    the tier is chosen (source='auto') with a short rationale. Falls back to the
    deterministic mock when nothing real is available (offline)."""
    cat = build_model_catalog(roster)
    models = cat["models"]
    by_id = {m["id"]: m for m in models}
    by_provider = {m["provider"]: m for m in models}

    if override:
        m = by_id.get(override) or next((x for x in models if x["model"] == override), None)
        if m and m["healthy"]:
            return {"provider": m["provider"], "model": m["model"], "tier": m["tier"],
                    "source": "override", "rationale": "Model pinned by an authorised reviewer in the Plan Review."}
        # invalid/unhealthy override → fall through to auto (the UI shows the reason)

    order = _LIGHT_ORDER if tier == "local" else _FRONTIER_ORDER
    for pid in order:
        m = by_provider.get(pid)
        if m and m["healthy"]:
            return {"provider": m["provider"], "model": m["model"], "tier": tier,
                    "source": "auto", "rationale": _RATIONALE.get(tier, _RATIONALE["frontier"])}

    healthy = [m for m in models if m["healthy"]]
    if healthy:
        m = healthy[0]
        return {"provider": m["provider"], "model": m["model"], "tier": tier,
                "source": "auto", "rationale": _RATIONALE.get(tier, _RATIONALE["frontier"])}
    return {"provider": "mock", "model": "mock-sdlc-1", "tier": tier,
            "source": "auto", "rationale": "No live provider configured — deterministic offline mock."}


def derive_plan_steps(
    *, template: int, roster: dict, step_overrides: dict[str, Any],
    outputs: list[str], skills: list[dict], tools: list[str], external_write_tools: set[str],
    gen_prompt_tokens: int, validation_enabled: bool = True,
) -> list[dict]:
    """Build the ordered, typed step list for a stage — the multi-model plan the
    reviewer sees before execution. Pure + deterministic (zero-token)."""
    def override_for(step_id: str) -> str | None:
        entry = step_overrides.get(step_id) or {}
        return entry.get("model") if isinstance(entry, dict) else None

    steps: list[dict] = []
    order = 0

    def add(**kw: Any) -> None:
        nonlocal order
        order += 1
        steps.append({"order": order, **kw})

    # 1) Generate — the heavy design/codegen call (has tools/context → frontier).
    add(id="generate", label=f"Generate {', '.join(outputs) or 'stage artifacts'}", kind="llm",
        model=resolve_step_model("frontier", roster, override_for("generate")),
        tools=[], skills=skills, editable=True, overlayKey="generate", estTokens=gen_prompt_tokens)

    # 2) Validate — a short judgement pass (light tier), if enabled.
    if validation_enabled:
        add(id="validate", label="Validate output against intent & for syntax", kind="llm",
            model=resolve_step_model("local", roster, override_for("validate")),
            tools=[], skills=[], editable=False, overlayKey=None, estTokens=700)

    # 3) Tools — compute + external writes (external writes are deferred to
    # post-approval). No model involved → 0 tokens.
    for t in tools:
        deferred = t in external_write_tools
        add(id=f"tool:{t}", label=t, kind="tool", model=None, tools=[t], skills=[],
            editable=False, overlayKey=None, estTokens=0, deferred=deferred)

    # 4) Gate — human sign-off (authorises any deferred publishes).
    add(id="gate", label="Human gate — reviewer sign-off", kind="gate", model=None,
        tools=[], skills=[], editable=False, overlayKey=None, estTokens=0)

    return steps
