"""Multi-model routing: which model serves which kind of work.

A *role* names a kind of work; each role holds an ordered chain of models
('provider/model'). A call tries the chain in order and falls back to the normal
gateway chain when the roles' models are all unavailable, so an unset or broken role
never blocks a run.

  reason    deep design reasoning (solution + technical architecture)
  generate  general artifact generation (requirements, tests, pipelines, code)
  light     small judging / summarising calls (validate, fact-check, clarify, ...)
  plan      the "Review plan" proposal
  vision    reading pictures, diagram pages, scans and slides

Routes are edited by a super-admin (applied live through Redis) with the env values
LIGHT_MODEL / PLAN_MODEL as defaults for those two roles.
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..domain.errors import SdlcError

ROLES: tuple[str, ...] = ("reason", "generate", "light", "plan", "vision")
# Roles a workflow stage may choose for its own generation.
STAGE_ROLES: tuple[str, ...] = ("reason", "generate", "light")
MAX_CHAIN = 4
PROVIDERS: tuple[str, ...] = ("bedrock", "groq", "grok", "gemini", "local")
SETTING_KEY = "model_routes"

ROLE_INFO: dict[str, dict[str, str]] = {
    "reason": {"label": "Reasoning", "description": "Deep design work: solution and technical architecture, "
                                                      "and the retry when the validator still finds problems."},
    "generate": {"label": "Generation", "description": "General artifact generation: requirements, tests, "
                                                       "pipelines, code."},
    "light": {"label": "Fast", "description": "Small judging and summarising calls: validator, fact-check, "
                                              "clarification, trait detection, context compression."},
    "plan": {"label": "Planning", "description": "The 'Review plan' proposal shown before a stage runs."},
    "vision": {"label": "Vision", "description": "Reading pictures, diagram pages, scanned pages and slides. "
                                                 "Must be a vision-capable model."},
}

# Which role each built-in stage template generates with (a stage can override it).
STAGE_DEFAULT_ROLE: dict[int, str] = {1: "generate", 2: "reason", 3: "reason", 4: "generate",
                                      5: "generate", 6: "generate", 7: "generate"}

_MODEL_RE = re.compile(r"^(?P<provider>[a-z0-9_-]+)/(?P<model>[A-Za-z0-9][A-Za-z0-9._:@/-]{0,199})$")


def role_for_stage(template: int | None, override: str | None = None) -> str:
    """The role a stage's generation uses: its explicit choice, else its template's default."""
    if override in STAGE_ROLES:
        return str(override)
    return STAGE_DEFAULT_ROLE.get(int(template or 1), "generate")


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [p for p in re.split(r"[,\n]", value)]
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        s = str(item).strip()
        if s and s not in out:
            out.append(s)
    return out


def parse_routes(raw: Any) -> dict[str, list[str]]:
    """Tolerant read of a stored value (JSON string or dict): unknown roles and malformed
    entries are dropped, never raised - a damaged setting must not take generation down."""
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for role in ROLES:
        chain = [m for m in _as_list(raw.get(role)) if _MODEL_RE.match(m)][:MAX_CHAIN]
        if chain:
            out[role] = chain
    return out


def validate_routes(routes: Any) -> dict[str, list[str]]:
    """Strict check for what an admin submits; raises SdlcError(VALIDATION_FAILED) naming the problem."""
    if not isinstance(routes, dict):
        raise SdlcError("VALIDATION_FAILED", "routes must be an object of role -> [model, ...]")
    clean: dict[str, list[str]] = {}
    for role, chain in routes.items():
        if role not in ROLES:
            raise SdlcError("VALIDATION_FAILED", f"unknown role '{role}' (expected one of {', '.join(ROLES)})")
        models = _as_list(chain)
        if len(models) > MAX_CHAIN:
            raise SdlcError("VALIDATION_FAILED", f"role '{role}' has more than {MAX_CHAIN} models")
        for m in models:
            match = _MODEL_RE.match(m)
            if not match:
                raise SdlcError("VALIDATION_FAILED",
                                f"'{m}' is not a valid model - use 'provider/model', e.g. 'bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0'")
            if match.group("provider") not in PROVIDERS:
                raise SdlcError("VALIDATION_FAILED",
                                f"unknown provider '{match.group('provider')}' in '{m}' (expected one of {', '.join(PROVIDERS)})")
        if models:
            clean[role] = models
    return clean


def env_defaults(settings: Any) -> dict[str, list[str]]:
    """Routes implied by the env settings (LIGHT_MODEL, PLAN_MODEL) - the baseline an admin overrides."""
    out: dict[str, list[str]] = {}
    for role, attr in (("light", "LIGHT_MODEL"), ("plan", "PLAN_MODEL")):
        chain = [m for m in _as_list(getattr(settings, attr, "") or "") if _MODEL_RE.match(m)][:MAX_CHAIN]
        if chain:
            out[role] = chain
    return out
