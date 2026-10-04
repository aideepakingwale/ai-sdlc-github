"""Refine and Plan agents.

The model *proposes* structured changes (LlmRefine / LlmPlan). Code sanitises them (proposals.py), stores a
proposal and renders the stage document FROM the sanitised proposal — so the document a reviewer reads can never
disagree with what will actually be applied. If the model is unavailable or returns nonsense the stage still
completes: Refine records "no AI changes" and Plan falls back to a deterministic pick by backlog rank."""

from __future__ import annotations

import logging
from typing import Any

from ..agents.phase_agents import AgentDeps, PhaseAgentResult, _add, _save_artifact  # type: ignore[attr-defined]
from ..domain.models import AgentState, ContextArtifact
from ..services.context import build_context_block
from pydantic import BaseModel, Field

from .proposals import LlmPlan, LlmRefine
from .specs import DesignDelta, component_slug, parse_spec, render_delta_md, section_hash, spec_path

log = logging.getLogger("agile")

DIGEST_ITEMS = 60


def backlog_digest(rows: list[Any], limit: int = DIGEST_ITEMS) -> str:
    """Compact, bounded view of the backlog for a prompt: counts + the top items by rank."""
    live = [r for r in rows if r["status"] != "dropped"]
    counts: dict[str, int] = {}
    for r in live:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    head = "Counts: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) if counts else "The backlog is empty."
    lines = []
    for r in sorted(live, key=lambda r: (r["rank"], r["created_at"]))[:limit]:
        est = f"{float(r['estimate']):g} pts" if r["estimate"] is not None else "unestimated"
        lines.append(f"{r['item_key']} | {r['type']} | {r['status']} | {est} | {r['title']}")
    more = len(live) - limit
    if more > 0:
        lines.append(f"… and {more} more lower-ranked items")
    return head + ("\n" + "\n".join(lines) if lines else "")


_REFINE_SYSTEM = (
    "#mock:agile_refine\n"
    "You are the Refinement agent of an Agile delivery team. Turn the product vision and the current backlog into a "
    "well-formed, prioritised backlog. Propose ONLY operations of type create, update or drop on the backlog shown. "
    "Rules: every story/bug/task needs 2-6 testable acceptance criteria; estimates use the scale "
    "0.5,1,2,3,5,8,13,21 (split anything above 13); group stories under epics; never duplicate an existing item; "
    "never change items already committed to a sprint; reference existing items by their key (e.g. DM-3). "
    'Return JSON: {"summary": str, "ops": [{"op": "create|update|drop", "target": "DM-n for update/drop", '
    '"ref": "short unique id", "type": "epic|story|bug|task", "title": str, "description": str, '
    '"acceptanceCriteria": [str], "estimate": number|null, "components": [str], "epic": "epic key or ref", '
    '"rationale": str}]}. Return at most 30 operations, most valuable first.'
)

_PLAN_SYSTEM = (
    "#mock:agile_plan\n"
    "You are the Sprint Planning agent. From the READY items listed, choose the scope for the sprint: highest value "
    "first, a coherent sprint goal, total points within the capacity, and no more items than the team can finish. "
    "Only choose items from the READY list, by key. "
    'Return JSON: {"goal": str, "picks": [{"key": "DM-n", "reason": str}], "risks": [str]}.'
)


def _ready_list(rows: list[Any]) -> str:
    out = []
    for r in sorted((x for x in rows if x["status"] == "ready" and not x["iteration_id"]),
                    key=lambda x: (x["rank"], x["created_at"])):
        est = f"{float(r['estimate']):g}" if r["estimate"] is not None else "?"
        out.append(f"READY: {r['item_key']} | {est} pts | {r['title']}")
    return "\n".join(out[:80]) or "(no ready items)"


async def _context(deps: AgentDeps, state: AgentState, emit: Any) -> str:
    block, compressed = await build_context_block(state.context_window, deps.settings.CONTEXT_TOKEN_THRESHOLD, deps.llm)
    if compressed:
        emit({"type": "node", "node": "compressor", "label": "Context compressed to fit token budget"})
    return block or "(none)"


async def run_refine(deps: AgentDeps, state: AgentState, emit: Any) -> PhaseAgentResult:
    persona = state.custom_persona or "Product Owner"
    emit({"type": "node", "node": "agent", "label": f"{persona} refining the backlog for '{state.stage_name}'"})
    rows = await deps.db.list_backlog(state.project_id)
    user = (
        f"## Instruction\n{state.user_input or 'Refine the backlog.'}\n\n"
        f"## Project\n{state.project_profile or state.tech_stack}\n\n"
        f"## Current backlog\n{backlog_digest(rows)}\n\n"
        f"## Approved context from earlier stages\n{await _context(deps, state, emit)}\n"
        + (f"\n## Extra context\n{state.extra_context}\n" if state.extra_context else "")
    )
    llm_out: LlmRefine
    unavailable = ""
    try:
        llm_out, result = await deps.llm.generate_json(
            intent="generation", tag=f"agile_refine{state.current_phase}", temperature=0.2, max_tokens=8192,
            schema=LlmRefine, model=state.model_overrides.get("generate") or None, max_attempts=2,
            messages=[{"role": "system", "content": _REFINE_SYSTEM}, {"role": "user", "content": user}])
        state.last_provider, state.last_model = result.provider, result.model
        deps.audit.record(project_id=state.project_id, phase=state.current_phase, agent_role=persona,
                          event="ai.generation", provider=result.provider, model=result.model,
                          prompt_tokens=result.usage["promptTokens"], completion_tokens=result.usage["completionTokens"],
                          artefact_body=result.content, detail={"agile": "refine"})
    except Exception as err:  # noqa: BLE001 — the stage must still complete
        log.warning("refine model call failed: %s", err)
        unavailable = f"The AI refinement could not be produced ({str(err)[:160]}). Edit the backlog manually."
        llm_out = LlmRefine(summary=unavailable)
    proposal = await deps.proposals.create_refine(
        state.project_id, state.current_phase, state.iteration_id, llm_out, [unavailable] if unavailable else None)
    md = render_refine_md(proposal, state.stage_name)
    artifacts: list[ContextArtifact] = []
    out_type = (state.custom_outputs or ["REFINEMENT_NOTES"])[0]
    _add(artifacts, await _save_artifact(deps, state, emit, type_=out_type, title=state.stage_name or "Refinement",
                                         content=md, summary=md[:300], exact=True))
    n = len(proposal["payload"]["ops"])
    return PhaseAgentResult(
        summary=f"Proposed {n} backlog change(s). Review them, then approve to apply.",
        new_artifacts=artifacts, gate_status="PENDING_REVIEW")


async def run_plan(deps: AgentDeps, state: AgentState, emit: Any) -> PhaseAgentResult:
    persona = state.custom_persona or "Scrum Master"
    emit({"type": "node", "node": "agent", "label": f"{persona} planning the sprint for '{state.stage_name}'"})
    iteration = await deps.db.get_iteration(state.iteration_id) if state.iteration_id else None
    if iteration is None:
        raise ValueError("this planning stage is not attached to a sprint")
    rows = await deps.db.list_backlog(state.project_id)
    cap = float(iteration["capacity"])
    user = (
        f"## Instruction\n{state.user_input or 'Plan the sprint.'}\n\n"
        f"## Sprint\nCAPACITY: {cap:g}\nGoal so far: {iteration['goal'] or '(none)'}\n\n"
        f"## Ready items (choose from these only)\n{_ready_list(rows)}\n\n"
        f"## Whole backlog (for context)\n{backlog_digest(rows, 30)}\n"
    )
    llm_out: LlmPlan | None
    note = ""
    try:
        llm_out, result = await deps.llm.generate_json(
            intent="generation", tag=f"agile_plan{state.current_phase}", temperature=0.2, max_tokens=4096,
            schema=LlmPlan, model=state.model_overrides.get("generate") or None, max_attempts=2,
            messages=[{"role": "system", "content": _PLAN_SYSTEM}, {"role": "user", "content": user}])
        state.last_provider, state.last_model = result.provider, result.model
        deps.audit.record(project_id=state.project_id, phase=state.current_phase, agent_role=persona,
                          event="ai.generation", provider=result.provider, model=result.model,
                          prompt_tokens=result.usage["promptTokens"], completion_tokens=result.usage["completionTokens"],
                          artefact_body=result.content, detail={"agile": "plan"})
    except Exception as err:  # noqa: BLE001
        log.warning("plan model call failed: %s", err)
        llm_out = None
        note = f"The AI plan could not be produced ({str(err)[:160]}); items were chosen by backlog rank."
    proposal = await deps.proposals.create_plan(
        state.project_id, state.current_phase, iteration, llm_out, [note] if note else None)
    md = render_plan_md(proposal, iteration, state.stage_name)
    artifacts: list[ContextArtifact] = []
    out_type = (state.custom_outputs or ["SPRINT_PLAN"])[0]
    _add(artifacts, await _save_artifact(deps, state, emit, type_=out_type, title=state.stage_name or "Sprint plan",
                                         content=md, summary=md[:300], exact=True))
    p = proposal["payload"]
    return PhaseAgentResult(
        summary=f"Proposed {len(p['items'])} item(s), {p['points']:g} of {cap:g} points. Approve to commit the sprint.",
        new_artifacts=artifacts, gate_status="PENDING_REVIEW")


AGILE_RUNNERS = {"refine": run_refine, "plan": run_plan}


# ------------------------------------------------------------------ deterministic documents
def render_refine_md(proposal: dict[str, Any], stage_name: str) -> str:
    p, warnings = proposal["payload"], proposal["warnings"]
    lines = [f"# {stage_name or 'Backlog refinement'}", "", p.get("summary") or "_No summary._", ""]
    if warnings:
        lines += ["## Needs attention", *[f"- {w}" for w in warnings], ""]
    groups = {"create": "New items", "update": "Changes to existing items", "drop": "Items to drop"}
    for op_name, heading in groups.items():
        ops = [o for o in p["ops"] if o["op"] == op_name]
        if not ops:
            continue
        lines += [f"## {heading} ({len(ops)})", ""]
        for o in ops:
            tag = o["target"] if o["op"] != "create" else o["type"]
            est = f" · {o['estimate']:g} pts" if o.get("estimate") is not None else ""
            lines.append(f"### {o['title'] or o['target']} — _{tag}_{est}")
            if o.get("description"):
                lines += ["", o["description"]]
            if o.get("acceptanceCriteria"):
                lines += ["", "Acceptance criteria:", *[f"- {c}" for c in o["acceptanceCriteria"]]]
            if o.get("rationale"):
                lines += ["", f"_Why:_ {o['rationale']}"]
            lines.append("")
    if not p["ops"]:
        lines += ["_No backlog changes proposed._", ""]
    return "\n".join(lines)


def render_plan_md(proposal: dict[str, Any], iteration: Any, stage_name: str) -> str:
    p, warnings = proposal["payload"], proposal["warnings"]
    lines = [f"# {stage_name or 'Sprint plan'} — {iteration['label']}", "",
             f"**Sprint goal:** {p.get('goal') or iteration['goal'] or '_not set_'}", "",
             f"**Scope:** {len(p['items'])} items, {p['points']:g} of {p['capacity']:g} points", ""]
    if warnings:
        lines += ["## Needs attention", *[f"- {w}" for w in warnings], ""]
    lines += ["## Committed items", "", "| Key | Item | Points | Why |", "|---|---|---|---|"]
    for i in p["items"]:
        lines.append(f"| {i['key']} | {i['title']} | {i['estimate']:g} | {i.get('reason', '')} |")
    if not p["items"]:
        lines.append("| — | _No ready items fit this sprint_ | | |")
    if p.get("risks"):
        lines += ["", "## Risks", *[f"- {r}" for r in p["risks"]]]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ Build: structured design delta
class LlmBuild(BaseModel):
    summary: str = ""
    designDelta: DesignDelta = Field(default_factory=DesignDelta)
    testDelta: str = ""
    incrementNotes: str = ""


_BUILD_SYSTEM = (
    "#mock:agile_build\n"
    "You are the Build & Test agent of an Agile team. For the committed sprint stories, describe (1) the DESIGN "
    "DELTA — only what changes in the living specs, as section-level changes — (2) the TEST DELTA — the tests "
    "added or changed for these stories — and (3) INCREMENT NOTES — what is delivered and any risks. "
    "Express design changes ONLY as changes to spec sections: op add|replace|remove, a component slug, the exact "
    "section heading, the full new section content in markdown. For replace/remove copy the baseHash shown for that "
    "section so concurrent edits are detected. One change per section, at most 15 changes. "
    'Return JSON: {"summary": str, "designDelta": {"summary": str, "changes": [{"component": str, "section": str, '
    '"op": "add|replace|remove", "content": str, "rationale": str, "baseHash": str|null}]}, '
    '"testDelta": markdown, "incrementNotes": markdown}.'
)


async def _spec_listing(deps: AgentDeps, project_id: str, components: list[str]) -> str:
    """Existing living-spec sections (with the hash to quote back) for the sprint's components, bounded."""
    index = getattr(deps, "index", None)
    if index is None:
        return "(no living specs yet)"
    ws = index.workspace(project_id)
    manifest = await ws.manifest()
    lines: list[str] = []
    for comp in components[:6]:
        path = spec_path(comp)
        if path not in manifest.files:
            continue
        _, sections = parse_spec(await ws.read(path))
        for name, body in list(sections.items())[:12]:
            lines.append(f"SPEC {comp} / {name} [hash={section_hash(body)}]: {' '.join(body.split())[:400]}")
    return "\n".join(lines) or "(no living specs yet for these components)"


async def run_build(deps: AgentDeps, state: AgentState, emit: Any) -> PhaseAgentResult:
    persona = state.custom_persona or "Senior Developer"
    emit({"type": "node", "node": "agent", "label": f"{persona} describing the increment for '{state.stage_name}'"})
    rows = await deps.db.list_backlog(state.project_id, iteration_id=state.iteration_id) if state.iteration_id else []
    comps = list(dict.fromkeys(c for r in rows for c in (r["components"] or [])))
    scope = "\n".join(
        f"{r['item_key']} | {r['title']} | AC: {'; '.join((r['acceptance_criteria'] or [])[:4])}" for r in rows[:40]
    ) or "(no committed items)"
    user = (
        f"## Instruction\n{state.user_input or 'Describe the increment.'}\n\n"
        f"## Committed sprint items\n{scope}\n\n## COMPONENTS: {', '.join(comps) or '(none declared)'}\n\n"
        f"## Living spec sections (quote baseHash when replacing/removing)\n{await _spec_listing(deps, state.project_id, comps)}\n\n"
        f"## Project\n{state.project_profile or state.tech_stack}\n"
        + (f"\n## Extra context\n{state.extra_context}\n" if state.extra_context else ""))
    unavailable = ""
    try:
        out, result = await deps.llm.generate_json(
            intent="generation", tag=f"agile_build{state.current_phase}", temperature=0.2, max_tokens=8192,
            schema=LlmBuild, model=state.model_overrides.get("generate") or None, max_attempts=2,
            messages=[{"role": "system", "content": _BUILD_SYSTEM}, {"role": "user", "content": user}])
        state.last_provider, state.last_model = result.provider, result.model
        deps.audit.record(project_id=state.project_id, phase=state.current_phase, agent_role=persona,
                          event="ai.generation", provider=result.provider, model=result.model,
                          prompt_tokens=result.usage["promptTokens"], completion_tokens=result.usage["completionTokens"],
                          artefact_body=result.content, detail={"agile": "build"})
    except Exception as err:  # noqa: BLE001
        log.warning("build model call failed: %s", err)
        unavailable = f"The AI increment description could not be produced ({str(err)[:160]})."
        out = LlmBuild(summary=unavailable)
    warn_extra = [unavailable] if unavailable else []
    # Components the sprint did not declare are allowed (a new spec may be needed) but are called out for review.
    declared = set(comps)
    for c in out.designDelta.changes:
        slug = component_slug(c.component)
        if declared and slug not in declared:
            warn_extra.append(f"'{slug}' is not a component of any committed story — check that this change belongs here")
    proposal = await deps.proposals.create_delta(state.project_id, state.current_phase, state.iteration_id,
                                                 out.designDelta, warn_extra)
    delta = DesignDelta.model_validate(proposal["payload"])
    docs = {
        "DESIGN_DELTA": render_delta_md(delta, proposal["warnings"], state.stage_name or "Design delta"),
        "TEST_DELTA": out.testDelta.strip() or "# Test delta\n\n_No test changes were described._\n",
        "INCREMENT_NOTES": out.incrementNotes.strip() or f"# Increment notes\n\n{out.summary or '_No notes._'}\n",
    }
    artifacts: list[ContextArtifact] = []
    for out_type in (state.custom_outputs or list(docs)):
        body = docs.get(out_type) or docs["INCREMENT_NOTES"]
        _add(artifacts, await _save_artifact(
            deps, state, emit, type_=out_type, title=f"{state.stage_name} — {out_type}",
            content=body, summary=body[:300], exact=True))
    return PhaseAgentResult(
        summary=f"Described the increment with {len(delta.changes)} design change(s). Approve to merge them into the living specs.",
        new_artifacts=artifacts, gate_status="PENDING_REVIEW")


AGILE_RUNNERS["build"] = run_build
