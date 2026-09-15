"""Skill system: predefined, role- and stage-scoped tasks a team member
can run against a project. Each skill declares:

  - phase   : the stage it belongs to (None = available in any stage)
  - roles   : phase-roles allowed to run it (SUPER_ADMIN may always run)
  - tier    : execution tier — non_llm (deterministic), local (lightweight
              model), or frontier (heavy reasoning). This wires the skill into
              the multi-model router.
  - tools   : MCP tools the skill may invoke

Non-LLM skills run deterministic Python (optionally via an MCP tool); local/
frontier skills call the LLM gateway at the declared tier. The UI shows only
the skills a given user may run at the project's current stage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from ..domain.errors import SdlcError
from ..domain.models import PhaseRole, UserPublic, get_phase
from .guardrails import enforce_input, sanitise_output
from .prompt_library import render as render_prompt
from .skill_loader import SkillPackError, load_skill_packs
from .telemetry import set_run_context

SkillTier = Literal["non_llm", "local", "frontier"]


@dataclass
class SkillContext:
    project_id: str
    phase: int
    tech_stack: str
    user: UserPublic
    user_input: str
    deps: Any  # AgentDeps-like: .llm, .mcp, .rag


@dataclass
class Skill:
    id: str
    name: str
    description: str
    phase: int | None
    roles: tuple[PhaseRole, ...]
    tier: SkillTier
    tools: tuple[str, ...]
    run: Callable[[SkillContext], Awaitable[dict[str, Any]]]
    input_hint: str = ""
    needs_input: bool = True


# ---------------------------------------------------------------- executors
async def _kb_search(ctx: SkillContext) -> dict[str, Any]:
    hits = await ctx.deps.rag.retrieve(ctx.user_input or "standards", ctx.project_id, top_k=6)
    lines = [f"- **{h['title']}** (score {h['score']}) — {h['content'][:160]}" for h in hits]
    return {"output": "\n".join(lines) or "No matches.", "meta": {"hits": len(hits)}}


async def _estimate_points(ctx: SkillContext) -> dict[str, Any]:
    # Deterministic Fibonacci estimate from scope signals — a genuine non-LLM task.
    text = ctx.user_input.lower()
    weight = len(text) / 120
    for kw, w in (("integration", 3), ("migration", 3), ("security", 2), ("realtime", 3),
                  ("report", 1), ("crud", 1), ("ui", 1), ("api", 1)):
        if kw in text:
            weight += w
    fib = [1, 2, 3, 5, 8, 13, 21]
    points = next((f for f in fib if f >= weight), 21)
    return {"output": f"Estimated **{points}** story points.", "meta": {"weight": round(weight, 1), "points": points}}


async def _lint_openapi(ctx: SkillContext) -> dict[str, Any]:
    # Non-LLM: lint the latest OpenAPI artifact via the Spectral MCP tool.
    rows = await ctx.deps.db.list_artefacts(ctx.project_id)
    oas = next((r for r in rows if r["type"] == "OPENAPI"), None)
    if not oas:
        raise SdlcError("NOT_FOUND", "No OpenAPI artifact in this project yet")
    body = oas["content"]
    if oas["storage_key"]:
        stored = await ctx.deps.content.get(oas["storage_key"])
        if stored:
            body = stored
    res = await ctx.deps.mcp.call("spectral_lint_openapi", {"openapiYaml": body})
    viol = res.get("violations", [])
    summary = f"Spectral: **{res['result']}** ({len(viol)} findings)"
    detail = "\n".join(f"- [{v['severity']}] {v['code']} @ {v['path']}: {v['message']}" for v in viol[:20])
    return {"output": f"{summary}\n{detail}", "meta": {"result": res["result"], "violations": len(viol)}}


async def _validate_pipeline(ctx: SkillContext) -> dict[str, Any]:
    # Non-LLM: check the CI workflow artifact for the 7 mandated stages.
    rows = await ctx.deps.db.list_artefacts(ctx.project_id)
    wf = next((r for r in rows if r["type"] == "GITHUB_ACTIONS"), None)
    if not wf:
        raise SdlcError("NOT_FOUND", "No CI workflow artifact in this project yet")
    body = wf["content"]
    if wf["storage_key"]:
        stored = await ctx.deps.content.get(wf["storage_key"])
        if stored:
            body = stored
    required = ["checkout", "lint", "build", "test", "snyk", "inspector", "deploy"]
    present = [s for s in required if s in body.lower()]
    missing = [s for s in required if s not in present]
    ok = not missing
    return {
        "output": f"Pipeline stages: **{len(present)}/7** present."
        + (f" Missing: {', '.join(missing)}." if missing else " All required stages present ✓"),
        "meta": {"present": present, "missing": missing, "pass": ok},
    }


async def _validate_diagram(ctx: SkillContext) -> dict[str, Any]:
    # Non-LLM: sanity-check Mermaid syntax of the input or the HLD diagram.
    src = ctx.user_input.strip()
    if not src:
        rows = await ctx.deps.db.list_artefacts(ctx.project_id)
        d = next((r for r in rows if r["type"] in ("HLD_DIAGRAM", "LLD_DIAGRAM")), None)
        if d:
            src = d["content"]
            if d["storage_key"]:
                src = await ctx.deps.content.get(d["storage_key"]) or src
    heads = ("flowchart", "graph", "sequencediagram", "classdiagram", "erdiagram", "statediagram")
    first = src.strip().splitlines()[0].lower().replace(" ", "") if src.strip() else ""
    valid = any(first.startswith(h) for h in heads)
    return {
        "output": ("✓ Valid Mermaid diagram" if valid else "✗ Not a recognised Mermaid diagram type")
        + f" (declares `{first[:30]}`)",
        "meta": {"valid": valid},
    }


async def _validate_drawio_pack(ctx: SkillContext) -> dict[str, Any]:
    # Non-LLM: structurally + qualitatively validate a draw.io diagram —
    # the pasted .drawio XML, or the latest DRAWIO artifact in the project. This is
    # the architect's self-check tool; it returns actionable findings.
    from .drawio import format_findings, validate_drawio

    src = ctx.user_input.strip()
    if not src:
        rows = await ctx.deps.db.list_artefacts(ctx.project_id)
        d = next((r for r in rows if r["type"] == "DRAWIO"), None)
        if not d:
            raise SdlcError("NOT_FOUND", "Paste a .drawio diagram, or generate an architecture stage first.")
        src = d["content"]
        if d["storage_key"]:
            src = await ctx.deps.content.get(d["storage_key"]) or src
    result = validate_drawio(src)
    return {"output": format_findings(result),
            "meta": {"valid": result["ok"], "errors": len(result["errors"]),
                     "warnings": len(result["warnings"]), **result.get("stats", {})}}


async def _latest_artifact_body(ctx: SkillContext, type_: str) -> str | None:
    """Newest artifact body of a type — content-store first, DB fallback."""
    rows = await ctx.deps.db.list_artefacts(ctx.project_id)
    row = next((r for r in rows if r["type"] == type_), None)
    if not row:
        return None
    if row["storage_key"]:
        stored = await ctx.deps.content.get(row["storage_key"])
        if stored:
            return stored
    return row["content"]


def _mcp_run_skill(artifact_type: str, tool: str, build_args: Callable[[str], dict[str, Any]],
                   summarise: Callable[[dict[str, Any]], str]) -> Callable[[SkillContext], Awaitable[dict[str, Any]]]:
    """Non-LLM skill executor: fetch the latest generated suite and run
    it through the matching MCP testing tool."""

    async def run(ctx: SkillContext) -> dict[str, Any]:
        body = await _latest_artifact_body(ctx, artifact_type)
        if not body:
            raise SdlcError("NOT_FOUND", f"No {artifact_type} artifact in this project yet — run its stage first")
        res = await ctx.deps.mcp.call(tool, build_args(body))
        return {"output": res.get("reportMarkdown", summarise(res)), "meta": {"tool": tool, **{
            k: v for k, v in res.items() if isinstance(v, (int, float, bool, str)) and k != "reportMarkdown"
        }}}

    return run


def _llm_skill(instruction: str, tier: SkillTier, mock_kind: str) -> Callable[[SkillContext], Awaitable[dict[str, Any]]]:
    """LLM-backed skill executor: the instruction is the skill pack's markdown
    body, composed with the Responsible AI policy preamble via the
    prompt-library wrapper."""

    async def run(ctx: SkillContext) -> dict[str, Any]:
        system = render_prompt(
            "skill.system.wrapper",
            policy=render_prompt("policy.responsible_ai"),
            instruction=instruction,
            tech_stack=ctx.tech_stack,
            mock_kind=mock_kind,
        )
        res = await ctx.deps.llm.generate(
            intent="generation", tier=("local" if tier == "local" else "frontier"),
            tag=f"skill:{mock_kind}", temperature=0.2, max_tokens=2048,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": ctx.user_input or "(no additional input)"},
            ],
        )
        return {"output": res.content, "meta": {"provider": res.provider, "tier": res.tier, "model": res.model}}

    return run


# ---------------------------------------------------------------- registry
# Skills are MARKDOWN FILES in services/orchestrator-py/skills/*.md: frontmatter
# declares identity + RBAC + execution wiring; the body is the instruction.
# Deterministic (non-LLM) executors are registered here by id and referenced
# from the pack via `executor: builtin`.
BUILTIN_EXECUTORS: dict[str, Callable[[SkillContext], Awaitable[dict[str, Any]]]] = {
    "kb_search": _kb_search,
    "estimate_points": _estimate_points,
    "lint_openapi": _lint_openapi,
    "validate_pipeline": _validate_pipeline,
    "validate_diagram": _validate_diagram,
    "validate_drawio": _validate_drawio_pack,
}


def _build_skill(pack: dict[str, Any]) -> Skill:
    executor = pack["executor"]
    if executor == "builtin":
        run = BUILTIN_EXECUTORS.get(pack["id"])
        if run is None:
            raise SkillPackError(f"{pack['file']}: no builtin executor registered for '{pack['id']}'")
    elif executor == "llm":
        run = _llm_skill(pack["body"], pack["tier"], pack.get("mock_kind", "chat"))
    else:  # mcp_run (loader guarantees the wiring keys exist)
        arg, extra = pack["mcp_arg"], pack.get("mcp_extra_args") or {}
        run = _mcp_run_skill(
            pack["artifact_type"], pack["mcp_tool"],
            lambda body, _arg=arg, _extra=extra: {_arg: body, **_extra},
            lambda r: ", ".join(f"{k}={v}" for k, v in list(r.items())[:3]),
        )
    return Skill(
        id=pack["id"], name=pack["name"], description=pack["description"],
        phase=pack.get("phase"), roles=tuple(pack["roles"]), tier=pack["tier"],
        tools=tuple(pack.get("tools") or ()), run=run,
        input_hint=pack.get("input_hint", ""),
        needs_input=bool(pack.get("needs_input", True)),
    )


SKILL_PACKS: list[dict[str, Any]] = load_skill_packs()
SKILLS: list[Skill] = [_build_skill(p) for p in SKILL_PACKS]

_BY_ID = {s.id: s for s in SKILLS}


def _can_run(skill: Skill, role: str, membership: str | None) -> bool:
    if role == "SUPER_ADMIN":
        return True
    if role == "PROJECT_MANAGER":
        return False # PMs orchestrate, they don't execute stage skills
    return membership in skill.roles


class SkillService:
    def __init__(self, db: Any, authz: Any, agent_deps: Any, workflow: Any = None) -> None:
        self._db = db
        self._authz = authz
        self._deps = agent_deps
        self._workflow = workflow

    async def _template_of(self, project_id: str, seq: int) -> int:
        """Map a workflow stage slot to its driving template; skills are
        declared against templates, not slots."""
        if self._workflow is None:
            return seq
        try:
            stage = await self._workflow.stage_by_seq(project_id, seq)
            return int(stage["template"])
        except Exception:
            return seq

    async def list_for(self, project_id: str, current_phase: int, user: UserPublic, phase: int | None) -> list[dict]:
        """Skills the user may run, scoped to `phase` (the focused stage) or the
        project's current phase. A skill shows when its stage's TEMPLATE matches
        (or it is global) AND the user's role can run it."""
        membership = None if user.role in ("SUPER_ADMIN", "PROJECT_MANAGER") else await self._authz.get_membership_role(project_id, user.id)
        target = await self._template_of(project_id, phase or current_phase)
        out = []
        for s in SKILLS:
            if s.phase is not None and s.phase != target:
                continue
            runnable = _can_run(s, user.role, membership)
            out.append({
                "id": s.id, "name": s.name, "description": s.description,
                "phase": s.phase, "tier": s.tier, "roles": list(s.roles),
                "tools": list(s.tools), "inputHint": s.input_hint,
                "needsInput": s.needs_input, "canRun": runnable,
            })
        return out

    async def execute(self, project_id: str, skill_id: str, user: UserPublic, user_input: str) -> dict:
        skill = _BY_ID.get(skill_id)
        if not skill:
            raise SdlcError("NOT_FOUND", f"Unknown skill {skill_id}")
        project = await self._db.get_project(project_id)
        if not project:
            raise SdlcError("NOT_FOUND", "Project not found")

        # Input guardrail: skill input reaches LLM prompts and MCP tools,
        # so it is screened exactly like a chat message.
        enforce_input(user_input, channel="skill")

        membership = None if user.role in ("SUPER_ADMIN", "PROJECT_MANAGER") else await self._authz.get_membership_role(project_id, user.id)
        if not _can_run(skill, user.role, membership):
            need = " or ".join(skill.roles)
            raise SdlcError("FORBIDDEN", f"Skill '{skill.name}' requires role {need} on this project")

        # Stage gate: a template-bound skill only runs while the
        # project's current stage is driven by that template.
        current_template = await self._template_of(project_id, project["current_phase"])
        if skill.phase is not None and skill.phase != current_template and user.role != "SUPER_ADMIN":
            raise SdlcError(
                "GATE_CONFLICT",
                f"'{skill.name}' belongs to {get_phase(skill.phase).name} work; "
                f"the project's current stage is driven by a different template",
            )

        set_run_context(project_id, project["current_phase"]) # span attribution
        ctx = SkillContext(
            project_id=project_id, phase=project["current_phase"],
            tech_stack=project.get("tech_stack") or "Node.js + TypeScript",
            user=user, user_input=user_input.strip(), deps=self._deps,
        )
        result = await skill.run(ctx)
        # Output guardrail: mask secrets/PII in skill output before it
        # reaches the UI; every mask is audited.
        safe_output, masked = sanitise_output(str(result.get("output", "")))
        result["output"] = safe_output
        if masked:
            self._deps.audit.record(
                project_id=project_id, phase=skill.phase, agent_role="OutputGuardrail",
                event="guardrail.output_masked", detail={"rules": masked, "skill": skill.id},
            )
        self._deps.audit.record(
            project_id=project_id, phase=skill.phase, agent_role="SkillRunner",
            event="skill.executed", human_reviewer=user.email,
            detail={"skill": skill.id, "tier": skill.tier, **result.get("meta", {})},
        )
        return {"skill": skill.id, "name": skill.name, "tier": skill.tier, **result}
