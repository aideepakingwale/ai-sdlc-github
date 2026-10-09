"""The specialist engine's view of the agent definitions in `services/orchestrator-py/agents/**/*.md`.

Each definition file is one agent: its instructions (the file body), the model role, the context it reads and the output fields it
owns. This module turns them into `Specialist` objects, validates them against the stage output schemas at boot, groups them into
waves and builds each agent's prompt. It holds no agent text of its own.

Specialist agents: one focused agent per artefact (or small family of artefacts) of a stage.

Instead of every artefact being written from the whole stage's prompt and context, each specialist has
  * its own instructions (what a good PRD / OpenAPI contract / sequence diagram is),
  * its own context window: only the upstream artefacts, sibling outputs and inputs it declares it needs,
  * the model role that suits the work (reason = deep design, generate = documents and code, light = short lists),
  * a record of exactly what it was given (prompt, context items, model), stored with the artefact.

A specialist owns one or more fields of its stage's output schema. Specialists whose `after` fields are not
finished wait for them (waves), so a diagram sees the components it draws without seeing anything else.
Fields no specialist owns keep the stage-wide path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..domain.models import AgentState, ContextArtifact, get_phase
from ..services.artifact_formats import norm_type
from ..services.prompt_library import render as render_prompt
from ..services.steering import resolve_steering
from ..services import agent_catalog
from .prompts import render_stack

REASON, GENERATE, LIGHT = "reason", "generate", "light"
UPSTREAM_ITEM_CHARS = 4_000          # most of one upstream artefact a specialist is shown
SIBLING_CHARS = 3_500                # most of one sibling output a specialist is shown
ATTACHMENT_CHARS = 30_000

BASE_RULES = (
    "You are a specialist working on ONE part of a larger deliverable. You see only the context below, which was chosen for you. "
    "Do not invent project facts that are not in it: where something is missing, state a clearly labelled assumption. "
    "Be concrete and consistent with the context; do not repeat it back."
)


@dataclass(frozen=True)
class Specialist:
    id: str
    name: str
    template: int                      # stage template this agent belongs to (1-6)
    kind: str                          # document | diagram | structured | code | list
    fields: tuple[str, ...]            # schema fields it writes
    artifacts: tuple[str, ...]         # artefact types those become
    role: str                          # model role: reason | generate | light
    instructions: str
    needs: tuple[str, ...] = ()        # upstream artefact types it reads ('*' = every upstream artefact)
    after: tuple[str, ...] = ()        # fields of THIS stage it reads from (runs after them)
    canon: bool = True                 # binding project rules + team memory
    stack: bool = False                # technology stack decision
    attachments: bool = False          # attached documents and pinned context
    steering: bool = False             # expert steering for the stage persona


def _from_definition(a: dict[str, Any]) -> Specialist:
    return Specialist(
        id=a["id"], name=a["name"], template=int(a["stage"]), kind=a["kind"], fields=tuple(a["fields"]), artifacts=tuple(a["artifacts"]),
        role=a["role"], instructions=a["body"], needs=tuple(a.get("upstream") or ()), after=tuple(a.get("after") or ()),
        canon=bool(a.get("canon", True)), stack=bool(a.get("stack", False)), attachments=bool(a.get("attachments", False)),
        steering=bool(a.get("steering", False)))


def _validate(specs: tuple[Specialist, ...]) -> None:
    """Fail fast at boot when a definition does not fit the stage's output schema."""
    from .schemas import PHASE_SCHEMAS
    for template in {s.template for s in specs}:
        fields = list(PHASE_SCHEMAS[template].model_fields)
        owned: dict[str, str] = {}
        for s in (x for x in specs if x.template == template):
            for f in s.fields:
                if f not in fields:
                    raise ValueError(f"agent '{s.id}': stage {template} has no output field '{f}'")
                if f in owned:
                    raise ValueError(f"agents '{owned[f]}' and '{s.id}' both write stage {template} field '{f}'")
                owned[f] = s.id
            for f in s.after:
                if f not in fields:
                    raise ValueError(f"agent '{s.id}': 'after' names unknown field '{f}'")


REGISTRY: tuple[Specialist, ...] = tuple(
    _from_definition(a) for a in agent_catalog.catalog() if a["runtime"] == "specialist" and a["status"] == "active")
_validate(REGISTRY)

BY_TEMPLATE: dict[int, list[Specialist]] = {}
for _sp in REGISTRY:
    BY_TEMPLATE.setdefault(_sp.template, []).append(_sp)
_BY_ID = {s.id: s for s in REGISTRY}


def get(agent_id: str) -> Specialist | None:
    return _BY_ID.get(agent_id)


def for_field(template: int, field_name: str) -> Specialist | None:
    return next((s for s in BY_TEMPLATE.get(template, []) if field_name in s.fields), None)


def for_type(template: int, artifact_type: str) -> Specialist | None:
    t = norm_type(artifact_type)
    return next((s for s in BY_TEMPLATE.get(template, []) if t in s.artifacts), None)


def units(template: int, fields: list[str]) -> list[Specialist]:
    """The specialists needed to produce `fields`, in registry order (each once)."""
    seen: dict[str, Specialist] = {}
    for f in fields:
        sp = for_field(template, f)
        if sp and sp.id not in seen:
            seen[sp.id] = sp
    return list(seen.values())


def waves(specs: list[Specialist]) -> list[list[Specialist]]:
    """Group into waves: a specialist runs after every specialist that produces a field it reads."""
    owner = {f: s.id for s in specs for f in s.fields}
    done: set[str] = set()
    pending = list(specs)
    out: list[list[Specialist]] = []
    while pending:
        ready = [s for s in pending if all(owner.get(f) in (None, s.id) or owner[f] in done for f in s.after)]
        if not ready:          # a cycle can't happen with the registry, but never loop forever
            ready = list(pending)
        out.append(ready)
        done |= {s.id for s in ready}
        pending = [s for s in pending if s.id not in done]
    return out


@dataclass
class Prompt:
    system: str
    user: str
    items: list[dict[str, Any]] = field(default_factory=list)       # what went in, with sizes
    recorded_system: str = ""                                          # the system prompt as stored (personal memory redacted)


def _clip(text: str, limit: int) -> tuple[str, int]:
    text = text or ""
    return (text if len(text) <= limit else text[:limit].rstrip() + "\n[… shortened …]"), len(text)


def _digest(value: Any) -> str:
    from pydantic import BaseModel
    if isinstance(value, BaseModel):
        return value.model_dump_json(indent=1)
    if isinstance(value, list):
        return "\n".join(_digest(v) for v in value)
    return str(value)


def build_prompt(
    spec: Specialist, state: AgentState, *, canon_block: str, memory_all: str, memory_redacted: str, formwork_block: str,
    siblings: dict[str, Any], amend: str | None,
) -> Prompt:
    """The specialist's own prompt: its instructions plus only the context it declared it needs."""
    phase = get_phase(state.stage_template)
    persona = phase.agent_persona
    items: list[dict[str, Any]] = [{"layer": "instructions", "label": f"{spec.name} instructions", "chars": len(spec.instructions)}]
    head = [render_prompt("policy.responsible_ai"), f"You are the {spec.name} ({persona}). {BASE_RULES}", spec.instructions,
            f"#mock:phase{state.stage_template}"]   # a directive the offline mock model keys on; real models ignore it
    if spec.steering:
        head.append(resolve_steering(persona))
    if spec.stack:
        head.append(render_stack(state.tech_stack, owner=False, source=state.tech_stack_source))
        items.append({"layer": "project", "label": f"Technology stack: {state.tech_stack or 'undecided'}", "chars": len(state.tech_stack or "")})
    if state.project_profile:
        head.append(f"## Project profile\n{state.project_profile}")
        items.append({"layer": "project", "label": "Project profile", "chars": len(state.project_profile)})
    rules = canon_block if spec.canon else ""
    if rules:
        items.append({"layer": "canon", "label": "Project canon", "chars": len(canon_block)})
    if formwork_block:
        head.append(formwork_block)
        items.append({"layer": "canon", "label": "Output template", "chars": len(formwork_block)})
    base = "\n\n".join(p for p in head if p)
    memory_chars = len(memory_all) if spec.canon and memory_all else 0
    if memory_chars:
        items.append({"layer": "memory", "label": "Team memory", "chars": memory_chars})

    def join(mem: str) -> str:
        return "\n\n".join(p for p in (base, rules, mem if spec.canon else "") if p)

    instruction = re.split(r"##\s*Production scope", state.user_input or "", maxsplit=1)[0].strip()
    parts = [f"## Brief\n{instruction}" if instruction else ""]
    items.append({"layer": "input", "label": "Reviewer's brief", "chars": len(instruction)})
    if amend:
        parts.append(f"## Changes requested\n{amend}")
        items.append({"layer": "revision", "label": "Changes requested", "chars": len(amend)})
    wanted = {norm_type(t) for t in spec.needs}
    ups: list[ContextArtifact] = [a for a in state.context_window if "*" in spec.needs or norm_type(a.type) in wanted]
    if ups:
        parts.append("## Approved context from earlier stages")
        for a in ups:
            body, total = _clip(a.content or a.summary or "", UPSTREAM_ITEM_CHARS)
            parts.append(f"### [Stage {a.phase}] {a.type}: {a.title}\n{body}")
            items.append({"layer": "upstream", "label": f"[Stage {a.phase}] {a.type}: {a.title}", "chars": len(body), "totalChars": total})
    sib = [(f, siblings[f]) for f in spec.after if f in siblings]
    if sib:
        parts.append("## Outputs of this stage you build on")
        for f, v in sib:
            body, total = _clip(_digest(v), SIBLING_CHARS)
            parts.append(f"### {f}\n{body}")
            items.append({"layer": "sibling", "label": f"This stage: {f}", "chars": len(body), "totalChars": total})
    if spec.attachments and state.extra_context:
        body, total = _clip(state.extra_context, ATTACHMENT_CHARS)
        parts.append(f"## Attached material and pinned context\n{body}")
        items.append({"layer": "attached", "label": "Attached material", "chars": len(body), "totalChars": total})
    return Prompt(system=join(memory_all), user="\n\n".join(p for p in parts if p), items=items, recorded_system=join(memory_redacted))
