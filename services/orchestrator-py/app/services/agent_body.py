"""The shape of a custom agent or skill definition, and how it is cleaned up when saved.

A definition is a JSON document (`body`) stored per version. Saving is forgiving: text is trimmed, enumerations are checked, limits are
enforced, but an unfinished draft (a prompt that uses an input nobody declared yet) is allowed. Whether a definition is *fit to be used*
is the audit's job (agent_audit.py), not the save's.

    agent body = {description, prompt, role, model, fallback, temperature, icon, stage_kind, execution_mode, inputs[], outputs[], children[], tools[]}
    skill body = {description, prompt, role, model, fallback, temperature, icon, inputs[], outputs[], roles[], stages[]}

Input `source` says where a value comes from: `brief` (the stage brief), `upstream:<TYPE>` (an approved artefact of that type),
`context:rules`, `context:stack`, or `user` (typed or pinned when the agent is run on request, tested, or run as a skill).
"""
from __future__ import annotations

import re
from typing import Any

from ..domain.errors import SdlcError
from ..domain.models import PHASE_ROLES

KINDS = ("agent", "skill")
MODEL_ROLES = ("reason", "generate", "light", "vision")
TYPES = ("string", "number", "boolean", "object", "list")
FORMATS = ("JSON", "Markdown", "Text")
STAGE_KINDS = ("specialist", "stage")
EXEC_MODES = ("native_llm",)             # an external webhook agent is a later release
SOURCES_FIXED = ("brief", "context:rules", "context:stack", "user")
RUNS = ("always", "when", "on_request")

MAX_INPUTS, MAX_OUTPUTS, MAX_CHILDREN = 12, 8, 5
MAX_PROMPT, MAX_DESC, MAX_NAME = 8_000, 400, 80
MIN_CAP, MAX_CAP = 1_000, 40_000

_IDENT = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_ARTEFACT = re.compile(r"^[A-Z][A-Z0-9_]{1,39}$")
_SOURCE = re.compile(r"^(brief|user|context:rules|context:stack|upstream:[A-Z][A-Z0-9_]{1,39})$")

ROLE_LABEL = {"reason": "Reasoning", "generate": "Generation", "light": "Fast", "vision": "Vision"}


def default_body(kind: str) -> dict[str, Any]:
    if kind == "skill":
        return normalise_body("skill", {"description": "", "prompt": "Describe what this skill does and what a good answer looks like.\nUse {input} for what the person types.",
                                        "role": "generate", "inputs": [{"name": "input", "type": "string", "source": "user", "required": True}],
                                        "outputs": [{"name": "result", "type": "string", "artefact_type": "REPORT", "format": "Markdown"}], "roles": [], "stages": []})
    return normalise_body("agent", {"description": "", "prompt": "You are an assistant that...\nUse {input_1}.", "role": "generate",
                                    "inputs": [{"name": "input_1", "type": "string", "source": "brief", "required": True}],
                                    "outputs": [{"name": "result", "type": "string", "artefact_type": "REPORT", "format": "Markdown"}]})


def _bad(msg: str) -> SdlcError:
    return SdlcError("VALIDATION_FAILED", msg)


def _text(v: Any, limit: int, what: str) -> str:
    s = str(v or "").replace("\r\n", "\n").strip()
    if len(s) > limit:
        raise _bad(f"{what} is longer than {limit} characters")
    return s


def clean_name(v: Any) -> str:
    n = " ".join(str(v or "").split())
    if len(n) < 3:
        raise _bad("A name needs at least 3 characters")
    if len(n) > MAX_NAME:
        raise _bad(f"A name is at most {MAX_NAME} characters")
    return n


def _cap(v: Any) -> int | None:
    """The most tokens one run of this agent (with its delegates) may spend. Empty means the platform limit."""
    if v in (None, "", 0):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError) as err:
        raise _bad("The token cap is a whole number") from err
    if not MIN_CAP <= n <= MAX_CAP:
        raise _bad(f"The token cap is between {MIN_CAP:,} and {MAX_CAP:,}")
    return n


def _inputs(raw: Any) -> list[dict[str, Any]]:
    out, seen = [], set()
    for i in (raw or []):
        name = str((i or {}).get("name") or "").strip()
        if not _IDENT.match(name):
            raise _bad(f"Input name '{name}': lower-case letters, numbers and underscores, starting with a letter")
        if name in seen:
            raise _bad(f"The input '{name}' appears twice")
        seen.add(name)
        typ = i.get("type") or "string"
        src = i.get("source") or "brief"
        if typ not in TYPES:
            raise _bad(f"Input '{name}': type must be one of {', '.join(TYPES)}")
        if not _SOURCE.match(src):
            raise _bad(f"Input '{name}': a source is brief, user, context:rules, context:stack or upstream:<TYPE>")
        out.append({"name": name, "type": typ, "source": src, "required": bool(i.get("required", True)), "description": _text(i.get("description"), 200, "An input description")})
    if len(out) > MAX_INPUTS:
        raise _bad(f"At most {MAX_INPUTS} inputs")
    return out


def _outputs(raw: Any) -> list[dict[str, Any]]:
    out, seen = [], set()
    for o in (raw or []):
        name = str((o or {}).get("name") or "").strip()
        if not _IDENT.match(name):
            raise _bad(f"Output name '{name}': lower-case letters, numbers and underscores, starting with a letter")
        if name in seen:
            raise _bad(f"The output '{name}' appears twice")
        seen.add(name)
        typ = o.get("type") or "string"
        art = str(o.get("artefact_type") or "REPORT").strip().upper()
        fmt = o.get("format") or ("JSON" if typ in ("object", "list") else "Markdown")
        if typ not in TYPES:
            raise _bad(f"Output '{name}': type must be one of {', '.join(TYPES)}")
        if not _ARTEFACT.match(art):
            raise _bad(f"Output '{name}': an artefact type is capital letters, numbers and underscores (for example RISK_ASSESSMENT)")
        if fmt not in FORMATS:
            raise _bad(f"Output '{name}': format must be one of {', '.join(FORMATS)}")
        out.append({"name": name, "type": typ, "artefact_type": art, "format": fmt})
    if len(out) > MAX_OUTPUTS:
        raise _bad(f"At most {MAX_OUTPUTS} outputs")
    return out


def normalise_body(kind: str, data: dict[str, Any] | None) -> dict[str, Any]:
    if kind not in KINDS:
        raise _bad("A definition is an agent or a skill")
    d = data or {}
    role = d.get("role") or "generate"
    if role not in MODEL_ROLES:
        raise _bad(f"The model role must be one of {', '.join(MODEL_ROLES)}")
    try:
        temp = float(d.get("temperature", 0.3))
    except (TypeError, ValueError) as err:
        raise _bad("The temperature is a number from 0 to 1") from err
    if not 0.0 <= temp <= 1.0:
        raise _bad("The temperature is a number from 0 to 1")
    mode = d.get("execution_mode") or "native_llm"
    if mode not in EXEC_MODES:
        raise _bad("Only native model agents are available in this release")
    if d.get("tools"):
        raise _bad("Tools are not available to custom agents in this release")
    body: dict[str, Any] = {
        "description": _text(d.get("description"), MAX_DESC, "The description"),
        "prompt": _text(d.get("prompt"), MAX_PROMPT, "The prompt"),
        "role": role,
        "model": (str(d["model"]).strip() or None) if d.get("model") else None,
        "fallback": (str(d["fallback"]).strip() or None) if d.get("fallback") and d.get("fallback") != "None" else None,
        "temperature": round(temp, 2),
        "icon": str(d.get("icon") or ("shield" if kind == "agent" else "wand"))[:24],
        "execution_mode": mode,
        "tools": [],
        "budget_tokens": _cap(d.get("budget_tokens")),
        "inputs": _inputs(d.get("inputs")),
        "outputs": _outputs(d.get("outputs")),
    }
    if kind == "agent":
        sk = d.get("stage_kind") or "specialist"
        if sk not in STAGE_KINDS:
            raise _bad("An agent works inside a stage (specialist) or runs as a whole custom stage")
        body["stage_kind"] = sk
        children = []
        for c in (d.get("children") or []):
            aid = str((c or {}).get("agent_id") or "").strip()
            if not aid:
                raise _bad("A delegate needs an agent")
            if aid in [x["agent_id"] for x in children]:
                raise _bad("The same agent is delegated to twice")
            ver = c.get("version")
            children.append({"agent_id": aid, "version": int(ver) if ver not in (None, "") else None, "when": _text(c.get("when") or "always", 300, "A condition")})
        if len(children) > MAX_CHILDREN:
            raise _bad(f"At most {MAX_CHILDREN} delegates")
        body["children"] = children
    else:
        roles = [r for r in (d.get("roles") or [])]
        bad = [r for r in roles if r not in PHASE_ROLES]
        if bad:
            raise _bad(f"Unknown role(s): {', '.join(map(str, bad))}")
        stages = []
        for s in (d.get("stages") or []):
            try:
                n = int(s)
            except (TypeError, ValueError) as err:
                raise _bad("A stage is a number from 1 to 7") from err
            if not 1 <= n <= 7:
                raise _bad("A stage is a number from 1 to 7")
            stages.append(n)
        body["roles"] = list(dict.fromkeys(roles))
        body["stages"] = sorted(set(stages))
        if not body["outputs"]:
            body["outputs"] = [{"name": "result", "type": "string", "artefact_type": "REPORT", "format": "Markdown"}]
        if not body["inputs"]:       # a skill is run by a person typing something: that is its one input
            body["inputs"] = [{"name": "input", "type": "string", "source": "user", "required": False, "description": ""}]
    return body


def summary(body: dict[str, Any]) -> dict[str, Any]:
    """What a list shows: no prompt, just what it is for and what it reads and writes."""
    return {"description": body.get("description", ""), "role": body.get("role", "generate"), "inputs": [i["name"] for i in body.get("inputs", [])],
            "outputs": [o["name"] for o in body.get("outputs", [])], "children": len(body.get("children", [])), "roles": body.get("roles", []),
            "stages": body.get("stages", [])}


def core_to_body(a: dict[str, Any], kind: str) -> dict[str, Any]:
    """A best-effort starting point for a copy of a core agent or skill. The copy is an ordinary custom definition; it does not follow the original."""
    if kind == "skill":
        return normalise_body("skill", {"description": a.get("description", ""), "prompt": a.get("body", ""), "role": "generate" if a.get("tier") != "local" else "light",
                                        "inputs": [{"name": "input", "type": "string", "source": "user", "required": False, "description": a.get("input_hint", "")}],
                                        "outputs": [{"name": "result", "type": "string", "artefact_type": "REPORT", "format": "Markdown"}],
                                        "roles": [r for r in (a.get("roles") or []) if r in PHASE_ROLES], "stages": [a["phase"]] if a.get("phase") else []})
    ups = list(a.get("upstream") or [])
    inputs = [{"name": re.sub(r"[^a-z0-9]+", "_", u.lower()).strip("_")[:39] or "input", "type": "string", "source": f"upstream:{u}", "required": False} for u in ups if re.match(r"^[A-Z][A-Z0-9_]{1,39}$", u)]
    inputs = inputs or [{"name": "brief", "type": "string", "source": "brief", "required": True}]
    outs = [{"name": re.sub(r"[^a-z0-9]+", "_", f.lower()).strip("_")[:39] or "result", "type": "string", "artefact_type": (a.get("artifacts") or ["REPORT"])[0] if re.match(r"^[A-Z][A-Z0-9_]{1,39}$", (a.get("artifacts") or ["REPORT"])[0]) else "REPORT",
             "format": "Markdown"} for f in (a.get("fields") or [])[:3]] or [{"name": "result", "type": "string", "artefact_type": "REPORT", "format": "Markdown"}]
    return normalise_body("agent", {"description": a.get("description", ""), "prompt": a.get("body", "")[:MAX_PROMPT], "role": a.get("role") if a.get("role") in MODEL_ROLES else "generate",
                                    "inputs": inputs[:MAX_INPUTS], "outputs": outs})
