"""Two-step code generation for the implementation stage.

STEP 1 (propose): the agent plans the repository - directories, files with a one-line purpose, naming
conventions, branch/commit/PR text - and saves it as the CODE_STRUCTURE artifact plus a `code_plans` row.
The stage then waits at its gate: the authorised reviewers approve (or request changes to) the STRUCTURE.
No code exists yet.

STEP 2 (implement): started only by that approval. The agent writes exactly the approved files, in
batches; a file outside the agreed structure is never accepted. The result is reviewed at the same gate
(now the CODE review); its approval publishes the queued GitHub branch + commit.

Every step is recorded in the audit trail; the proposal, its decision and the generated files are stored.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..domain.errors import SdlcError
from ..domain.models import AgentState, ContextArtifact
from ..services import code_structure as cs
from ..services.context import build_context_block
from ..services.model_routes import role_for_stage
from ..services.prompt_library import render as render_prompt
from ..services.scaffold import quality_gate_files
from ..services.steering import resolve_steering
from .prompts import render_stack
from .schemas import CodeBatchOutput, CodeStructureOutput

log = logging.getLogger("code_generation")

PLAN_BLOCK_CHARS = 14_000
BATCH_CONCURRENCY = 3


def enabled(deps: Any) -> bool:
    return bool(getattr(deps.settings, "CODE_TWO_STEP_ENABLED", True))


def _platform_files(deps: Any, state: AgentState) -> dict[str, str]:
    """The deterministic quality-gate files (coverage + lint config): written by the platform, not the model."""
    if not getattr(deps.settings, "QUALITY_GATE_ENABLED", True):
        return {}
    det = quality_gate_files(state.tech_stack, getattr(deps.settings, "COVERAGE_MIN_PERCENT", 80),
                             getattr(deps.settings, "LINT_REQUIRED", True))
    return {d["path"]: d["content"] for d in (det or [])}


def _system_prefix(state: AgentState, persona: str) -> str:
    steering = resolve_steering(persona)
    profile = f"## Project profile\n{state.project_profile}\n\n" if state.project_profile else ""
    return render_prompt("policy.responsible_ai") + "\n\n" + (f"{steering}\n\n" if steering else "") + profile


async def run(deps: Any, state: AgentState, emit: Any):
    """Entry point from the phase-6 runner: step 2 when a structure is approved, otherwise step 1."""
    plan = await deps.db.latest_code_plan(state.project_id, state.current_phase)
    if plan and plan["status"] == "approved":
        return await implement(deps, state, emit, plan)
    return await propose(deps, state, emit, plan)


# ------------------------------------------------------------------ step 1
async def propose(deps: Any, state: AgentState, emit: Any, previous: dict[str, Any] | None):
    from .phase_agents import PhaseAgentResult, _add, _save_artifact

    persona = "Implementation Lead"
    emit({"type": "node", "node": "agent", "label": f"{persona}: step 1 of 2 - planning the repository structure (no code yet)"})
    context_block, compressed = await build_context_block(state.context_window, deps.settings.CONTEXT_TOKEN_THRESHOLD, deps.llm)
    if compressed:
        emit({"type": "node", "node": "compressor", "label": "Context compressed to fit token budget"})

    feedback = ""
    if previous and previous["status"] == "proposed":
        listing = "\n".join(f"- {f['path']}: {f['purpose']}" for f in previous["structure"]["files"][:200])
        reviewer = (state.amend_comments or "").strip()
        feedback = ("## Your previous proposal was NOT approved - revise it\n"
                    + (f"Reviewer's requested changes:\n{reviewer}\n\n" if reviewer else "")
                    + f"Previous file plan:\n{listing}\n")
    user_input = state.user_input or "Plan the repository structure for the approved design."
    if state.extra_context:
        user_input = f"{user_input}\n\n{state.extra_context}"
    system = _system_prefix(state, persona) + render_prompt(
        "code.structure.system", persona=persona, stage_name=state.stage_name or "Implementation & Delivery",
        stack_block=render_stack(state.tech_stack, owner=False, source=state.tech_stack_source))
    user = render_prompt("code.structure.user", user_input=user_input, context_block=context_block or "(none)", feedback_block=feedback)
    data, result = await deps.llm.generate_json(
        intent="generation", tag=f"stage{state.current_phase}_code_structure", temperature=0.2,
        max_tokens=min(16_000, getattr(deps.settings, "PHASE_MAX_TOKENS", 16_000)), schema=CodeStructureOutput,
        model=state.model_overrides.get("generate") or None, role=state.model_role or role_for_stage(state.stage_template),
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    deps.audit.record(
        project_id=state.project_id, phase=state.current_phase, agent_role=persona, event="ai.generation",
        provider=result.provider, model=result.model, prompt_tokens=result.usage["promptTokens"],
        completion_tokens=result.usage["completionTokens"], artefact_body=result.content,
        detail={"step": "code_structure", "revision": bool(feedback)})
    state.last_provider, state.last_model = result.provider, result.model

    platform = _platform_files(deps, state)
    extra = [{"path": p, "purpose": "Coverage threshold, linter and formatter configuration (written by the platform, no model tokens)"}
             for p in platform]
    structure = cs.normalise_structure(data, extra_files=extra)
    meta = {"branch": data.branch.strip() or "feature/implementation", "commitMessage": data.commitMessage.strip(),
            "prTitle": data.prTitle.strip(), "prBody": data.prBody.strip(), "checklist": list(data.checklist)[:20]}
    row = await deps.db.insert_code_plan(project_id=state.project_id, phase=state.current_phase, structure=structure, meta=meta,
                                         artefact_id=None, proposed_by=persona)
    title = "Code structure & file plan"
    artifact = await _save_artifact(
        deps, state, emit, type_="CODE_STRUCTURE", title=title, content=cs.render_markdown(structure, meta, version=row["version"]),
        summary=f"{len(structure['files'])} files in {len(structure['directories'])} directories proposed for approval (v{row['version']})",
        exact=True)
    saved = await deps.db.latest_artefact_row(state.project_id, state.current_phase, "CODE_STRUCTURE", title)
    if saved:
        await deps.db.set_code_plan_artefact(row["id"], saved["id"])
    deps.audit.record(
        project_id=state.project_id, phase=state.current_phase, agent_role=persona, event="code.structure.proposed",
        detail={"version": row["version"], "files": len(structure["files"]), "directories": len(structure["directories"]),
                "hash": cs.structure_hash(structure), "branch": meta["branch"], "revision": bool(feedback),
                "artefactId": saved["id"] if saved else None},
        artefact_body=cs.render_markdown(structure, meta, version=row["version"]))
    emit({"type": "node", "node": "agent",
          "label": f"Structure proposed: {len(structure['files'])} files - waiting for approval before any code is written"})
    arts: list[ContextArtifact] = []
    _add(arts, artifact)
    return PhaseAgentResult(
        summary=(f"Step 1 of 2: proposed a repository structure of {len(structure['files'])} files in "
                 f"{len(structure['directories'])} directories (v{row['version']}). Review and approve it - no code is written "
                 "until the structure is approved."),
        new_artifacts=arts, gate_status="PENDING_REVIEW")


# ------------------------------------------------------------------ step 2
def _plan_block(structure: dict[str, Any]) -> str:
    lines = [f"- {f['path']} - {f['purpose']}" for f in structure["files"]]
    text = "\n".join(lines)
    return text if len(text) <= PLAN_BLOCK_CHARS else text[:PLAN_BLOCK_CHARS] + "\n- … (more files omitted)"


async def _write_batch(deps: Any, state: AgentState, system: str, structure: dict[str, Any], batch: cs.Batch,
                       context_block: str, emit: Any, total: int) -> dict[str, str]:
    purposes = {f["path"]: f for f in structure["files"]}

    async def attempt(paths: list[str], note: str = "") -> tuple[dict[str, str], list[str]]:
        batch_block = "\n".join(f"- {p} ({purposes[p]['kind']}): {purposes[p]['purpose']}" for p in paths)
        user = render_prompt("code.implement.user", user_input=state.user_input or "Implement the approved structure.",
                             batch_block=batch_block + (f"\n\n{note}" if note else ""), plan_block=_plan_block(structure),
                             context_block=context_block or "(none)", files_marker=f"<!-- files: {' | '.join(paths)} -->")
        data, result = await deps.llm.generate_json(
            intent="generation", tag=f"stage{state.current_phase}_code_batch{batch.index}", temperature=0.2,
            max_tokens=getattr(deps.settings, "PHASE_MAX_TOKENS", 16_000), schema=CodeBatchOutput,
            model=state.model_overrides.get("generate") or None, role=state.model_role or role_for_stage(state.stage_template),
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        deps.audit.record(
            project_id=state.project_id, phase=state.current_phase, agent_role="Implementation Lead", event="ai.generation",
            provider=result.provider, model=result.model, prompt_tokens=result.usage["promptTokens"],
            completion_tokens=result.usage["completionTokens"],
            detail={"step": "code_batch", "batch": batch.index, "files": paths})
        accepted, missing, unexpected = cs.check_batch(paths, [f.model_dump() for f in data.files])
        if unexpected:
            deps.audit.record(project_id=state.project_id, phase=state.current_phase, agent_role="Implementation Lead",
                              event="code.generation.rejected_files", detail={"batch": batch.index, "paths": unexpected[:20]})
        return accepted, missing

    done, missing = await attempt(batch.paths)
    if missing:
        more, missing = await attempt(missing, "These files were missing from your previous answer; write them now.")
        done.update(more)
    if missing:
        raise SdlcError("PROVIDER_ERROR", f"the model did not write {len(missing)} agreed file(s): {', '.join(missing[:5])}")
    emit({"type": "node", "node": "agent", "label": f"Batch {batch.index + 1}/{total} written ({len(done)} files)"})
    return done


async def implement(deps: Any, state: AgentState, emit: Any, plan: dict[str, Any]):
    from .phase_agents import PhaseAgentResult, _add, _phase6_verify, _publish, _save_artifact

    structure, meta = plan["structure"], plan["meta"]
    persona = "Implementation Lead"
    emit({"type": "node", "node": "agent",
          "label": f"{persona}: step 2 of 2 - writing the {len(structure['files'])} files of the approved structure"})
    context_block, _ = await build_context_block(state.context_window, deps.settings.CONTEXT_TOKEN_THRESHOLD, deps.llm)
    conventions = "\n".join(f"- {c}" for c in structure.get("conventions", [])) or "- Follow the stack's idiomatic conventions."
    system = _system_prefix(state, persona) + render_prompt(
        "code.implement.system", persona=persona, stage_name=state.stage_name or "Implementation & Delivery",
        stack_block=render_stack(state.tech_stack, owner=False, source=state.tech_stack_source), conventions=conventions)

    platform = {p.lower(): (p, c) for p, c in _platform_files(deps, state).items()}
    written: dict[str, str] = {}
    for f in structure["files"]:
        if f["path"].lower() in platform:
            written[f["path"]] = platform[f["path"].lower()][1]
    batches = cs.make_batches(structure, skip=set(written))
    deps.audit.record(project_id=state.project_id, phase=state.current_phase, agent_role=persona, event="code.generation.started",
                      detail={"version": plan["version"], "files": len(structure["files"]), "batches": len(batches),
                              "platformFiles": len(written), "hash": cs.structure_hash(structure)})
    sem = asyncio.Semaphore(BATCH_CONCURRENCY)

    async def guarded(b: cs.Batch) -> dict[str, str]:
        async with sem:
            return await _write_batch(deps, state, system, structure, b, context_block, emit, len(batches))

    for part in await asyncio.gather(*(guarded(b) for b in batches)):
        written.update(part)
    ordered = {f["path"]: written[f["path"]] for f in structure["files"] if f["path"] in written}

    # The branch + commit are queued and run when the CODE is approved at the gate (deferred publish).
    await _publish(deps, emit, "github_create_branch", {"branch": meta["branch"], "from": "main"})
    push = await _publish(deps, emit, "github_commit_code", {
        "branch": meta["branch"], "files": [{"path": p, "content": c} for p, c in ordered.items()], "message": meta["commitMessage"]})
    pr_body = meta.get("prBody", "")
    await _publish(deps, emit, "github_create_pull_request", {
        "branch": meta["branch"], "title": meta.get("prTitle") or "Implementation", "body": pr_body,
        "checklist": meta.get("checklist", [])})
    artifacts: list[ContextArtifact] = []
    kinds = {f["path"]: f["kind"] for f in structure["files"]}
    for path, content in ordered.items():
        is_test = kinds.get(path) == "test" or cs.is_test_path(path)
        _add(artifacts, await _save_artifact(
            deps, state, emit, type_="UNIT_TESTS" if is_test else "APP_CODE", title=path, content=content,
            url=push["htmlUrl"], summary=f"{'Test' if is_test else 'Source'} file {path} on {meta['branch']}", exact=True, source_path=path))
    await deps.db.mark_code_plan_implemented(plan["id"])
    deps.audit.record(project_id=state.project_id, phase=state.current_phase, agent_role=persona, event="code.generation.completed",
                      detail={"version": plan["version"], "files": len(ordered), "chars": sum(len(c) for c in ordered.values()),
                              "branch": meta["branch"]})
    emit({"type": "node", "node": "agent", "label": f"{len(ordered)} files written to the approved structure - awaiting code review"})
    verdicts, sonar_line = await _phase6_verify(deps, state, emit, [{"path": p, "content": c} for p, c in ordered.items()], artifacts)
    return PhaseAgentResult(
        summary=(f"Step 2 of 2: wrote {len(ordered)} files to the approved structure (v{plan['version']}). On approval of this code, "
                 f"branch `{meta['branch']}` is committed to GitHub. Verification: {' · '.join(verdicts) or 'n/a'}; {sonar_line}."),
        new_artifacts=artifacts, gate_status="PENDING_REVIEW")
