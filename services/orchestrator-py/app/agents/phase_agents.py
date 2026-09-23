"""The six phase agents (Modules 5+6): generate structured artifacts with the LLM,
execute MCP tool sequences, persist + RAG-index artifacts, and hand phase 6 to
the Build Recovery Loop. `emit` streams progress into the chat SSE."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pydantic import BaseModel

from ..config import Settings
from ..domain.errors import SdlcError
from ..domain.models import AgentState, ArtifactRef, ContextArtifact, PhaseStatus, get_phase
from ..integrations.llm import LlmClient
from ..integrations.mcp_client import McpToolClient
from ..repos.pg import Database, new_id
from ..services.audit import AuditService
from ..services.content_store import ContentStore, artifact_key, source_key
from ..services.content_validators import format_issues, syntactic_issues
from ..services.context import build_context_block
from ..services.diagram_render import render_architecture
from ..services.guardrails import sanitise_output
from ..services.prompt_library import render as render_prompt
from ..services.steering import resolve_steering
from ..services.rag import RagService
from ..services.scaffold import quality_gate_files
from .prompts import build_phase_prompt, openapi_fix_prompt
from .schemas import (
    PHASE_SCHEMAS,
    CloudArchitecture,
    CustomPhaseOutput,
    DiagramEdge,
    DiagramNode,
    FileEntry,
    OpenapiFix,
    Phase1Output,
    Phase2Output,
    Phase3Output,
    Phase4Output,
    Phase5Output,
    Phase6Output,
    ValidationIssue,
    ValidationVerdict,
)

log = logging.getLogger("agents")

Emit = Callable[[dict[str, Any]], None]


@dataclass
class AgentDeps:
    llm: LlmClient
    mcp: McpToolClient
    db: Database
    audit: AuditService
    rag: RagService
    content: ContentStore
    monitor: Any  # BuildMonitor (typed loosely to avoid a cycle)
    settings: Settings
    telemetry: Any = None # TelemetryService; optional so tests stay lean
    canon: Any = None # CanonService — binding project rules
    formworks: Any = None # FormworkService — output templates


@dataclass
class PhaseAgentResult:
    summary: str
    new_artifacts: list[ContextArtifact]
    gate_status: PhaseStatus
    # Deferred external-write tool calls captured during generation: these
    # are NOT executed until the phase's HITL gate is approved. Empty when
    # publish-on-approval is disabled or the phase makes no external writes.
    publish_actions: list[dict[str, Any]] = field(default_factory=list)


# External, side-effecting WRITE tools. A stage that would create Jira
# tickets, publish Confluence pages, or commit design/config docs to GitHub must
# NOT do so during generation — those actions are queued and replayed only after
# the gate is approved, by the approver. Keyed by TOOL NAME so it is
# workflow-agnostic (the six templates are fixed, but stages are dynamic).
# Template 6's code push / PR / CI recovery loop is intentionally NOT deferred:
# the PR itself is the delivery + review mechanism.
EXTERNAL_WRITE_TOOLS: frozenset[str] = frozenset({
    "jira_create_epic", "jira_create_story", "jira_create_xray_test",
    "confluence_publish_prd", "confluence_publish_hld", "confluence_publish_lld",
    "github_commit_diagrams", "github_commit_lld_artefacts", "github_commit_pipeline_config",
})

# Per-run collector for deferred publish actions. A list means "defer"; None means
# "execute immediately" (legacy behaviour / direct tool tests). ContextVars are
# task-local and propagate across awaits, so the phase runners need no new params.
_publish_sink: ContextVar[list[dict[str, Any]] | None] = ContextVar("publish_sink", default=None)

# A pending artifact URL uses this scheme while the external write is deferred;
# publication back-patches it to the real URL on gate approval.
PENDING_URL_PREFIX = "pending://"


def _deferred_stub(tool: str, args: dict[str, Any], n: int) -> dict[str, Any]:
    """A deterministic stand-in result for a deferred external write, carrying the
    keys/URLs the phase agent reads downstream. The `pending://` URL is a sentinel
    the publisher matches to back-patch the real URL after approval."""
    token = f"{PENDING_URL_PREFIX}{tool}-{n}"
    if tool == "jira_create_epic":
        key = re.sub(r"[^A-Z0-9]", "", str(args.get("projectKey") or "PROJ").upper()) or "PROJ"
        return {"epicId": f"pending-{n}", "epicKey": f"{key}-E{n}", "url": token}
    if tool == "jira_create_story":
        key = str(args.get("epicKey") or "PROJ").split("-")[0] or "PROJ"
        return {"storyId": f"pending-{n}", "storyKey": f"{key}-S{n}", "url": token}
    if tool == "jira_create_xray_test":
        key = str(args.get("storyKey") or "PROJ").split("-")[0] or "PROJ"
        return {"xrayTestKey": f"{key}-T{n}", "url": token}
    if tool.startswith("confluence_publish"):
        return {"pageId": f"pending-{n}", "url": token}
    if tool.startswith("github_commit"):
        return {"commitSha": f"pending{n}", "htmlUrl": token}
    return {"url": token}


async def _publish(deps: AgentDeps, emit: Emit, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Route an external WRITE through the deferral gate. When a publish
    sink is active (generation, pre-gate), the call is queued and a deterministic
    stub is returned so generation completes without touching Jira/Confluence/
    GitHub. When no sink is active, it executes immediately (legacy path)."""
    sink = _publish_sink.get()
    if sink is None:
        return await _tool(deps, emit, name, args)
    stub = _deferred_stub(name, args, len(sink) + 1)
    sink.append({"tool": name, "args": args, "stub": stub})
    emit({"type": "tool_call", "tool": name, "status": "deferred",
          "summary": "queued — publishes to the external tool after gate approval"})
    return stub


async def _save_artifact(
    deps: AgentDeps, state: AgentState, emit: Emit, *,
    type_: str, title: str, content: str, summary: str,
    url: str | None = None, exact: bool = False, ref_key: str | None = None,
    source_path: str | None = None,
) -> ContextArtifact:
    # Output guardrail: mask secrets/PII before the body is persisted
    # anywhere (content store, DB, RAG index) — masks are audited.
    content, masked = sanitise_output(content)
    if masked:
        deps.audit.record(
            project_id=state.project_id, phase=state.current_phase, agent_role="OutputGuardrail",
            event="guardrail.artifact_masked", detail={"rules": masked, "type": type_, "title": title},
        )

    # Persist the body to the content-store tier; DB keeps a pointer.
    artefact_id = new_id()
    key = (
        source_key(state.project_id, state.current_phase, source_path)
        if source_path
        else artifact_key(state.project_id, state.current_phase, type_, artefact_id)
    )
    await deps.content.put(key, content)
    # Deferred publish: a `pending://` URL is a sentinel kept in the DB row
    # so publication can back-patch the real external URL after approval; it is
    # NOT surfaced as a clickable link until then.
    pending = bool(url and url.startswith(PENDING_URL_PREFIX))
    ref_url = None if pending else url
    # Versioning: link this generation to the prior version of the same logical
    # artifact (phase/type/title). On amend/retrigger the previous rows are kept
    # as superseded history; this one becomes the latest at version+1.
    prev = await deps.db.latest_artefact_version(state.project_id, state.current_phase, type_, title)
    lineage_id = prev["lineage_id"] if prev else artefact_id
    version = (prev["version"] + 1) if prev else 1
    await deps.db.insert_artefact(
        project_id=state.project_id, phase=state.current_phase,
        type_=type_, title=title, content=content, url=url,
        storage_key=key, storage_mode=deps.content.mode, artefact_id=artefact_id,
        lineage_id=lineage_id, version=version,
    )
    artifact = ContextArtifact(
        phase=state.current_phase, type=type_, title=title, summary=summary,
        exact=exact, content=content if exact else None,
        ref=ArtifactRef(url=ref_url, key=ref_key),
    )
    await deps.rag.index_artifact(state.project_id, artefact_id, artifact) #
    emit({"type": "artifact", "artifact": {"type": type_, "title": title, "url": url, "key": ref_key}})
    return artifact


async def _generate(deps: AgentDeps, state: AgentState, emit: Emit, *, rework: str | None = None) -> BaseModel:
    # Generation is driven by the stage's TEMPLATE; the stage's own
    # name/seq come from the workflow config.
    phase = get_phase(state.stage_template)
    label_name = state.stage_name or phase.name
    emit({"type": "node", "node": "agent",
          "label": f"{phase.agent_persona} generating for '{label_name}' ({', '.join(phase.produces)})"})

    context_block, compressed = await build_context_block(
        state.context_window, deps.settings.CONTEXT_TOKEN_THRESHOLD, deps.llm
    )
    if compressed:
        emit({"type": "node", "node": "compressor", "label": "Context compressed to fit token budget"})

    snippets = await deps.rag.retrieve(state.user_input, state.project_id)
    if snippets:
        emit({"type": "node", "node": "agent",
              "label": f"RAG: retrieved {len(snippets)} knowledge snippet(s) for grounding"})

    # Project Canon + Output Formworks: human-authored, binding context.
    canon_block = ""
    if deps.canon is not None:
        canon_block = await deps.canon.render_block(state.project_id, state.stage_template)
        if canon_block:
            emit({"type": "node", "node": "agent",
                  "label": "Canon: applying the project's binding rules and decisions"})
    formwork_block = ""
    if deps.formworks is not None:
        formwork_block = await deps.formworks.render_block(state.project_id, list(phase.produces))
        if formwork_block:
            emit({"type": "node", "node": "agent",
                  "label": "Formwork: shaping output to the project's approved templates"})

    # Validation rework: the validation agent's modification instructions
    # ride the same amend channel as reviewer feedback, so the phase agent fixes
    # the flagged defects while preserving what was already correct.
    amend_comments = state.amend_comments
    if rework:
        amend_comments = (f"{amend_comments}\n\n" if amend_comments else "") + rework

    system, user = build_phase_prompt(
        phase=state.stage_template,
        context_block=context_block,
        rag_block=deps.rag.render_block(snippets),
        user_input=state.user_input,
        amend_comments=amend_comments,
        tech_stack=state.tech_stack,
        project_profile=state.project_profile,
        has_codebase=state.has_codebase,
        canon_block=canon_block,
        formwork_block=formwork_block,
        user_context_block=state.extra_context,
        quality_gate_enabled=getattr(deps.settings, "QUALITY_GATE_ENABLED", True),
        coverage_min=getattr(deps.settings, "COVERAGE_MIN_PERCENT", 80),
        lint_required=getattr(deps.settings, "LINT_REQUIRED", True),
    )
    if state.extra_context:
        emit({"type": "node", "node": "agent",
              "label": "Using the context you attached (references + files) for this stage"})
    data, result = await deps.llm.generate_json(
        intent="architecture" if state.stage_template <= 3 else "generation",
        tag=f"stage{state.current_phase}_template{state.stage_template}_agent",
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        schema=PHASE_SCHEMAS[state.stage_template],
        model=state.model_overrides.get("generate") or None, # per-step model override
    )
    deps.audit.record(
        project_id=state.project_id, phase=state.current_phase, agent_role=phase.agent_persona,
        event="ai.generation", provider=result.provider, model=result.model,
        prompt_tokens=result.usage["promptTokens"], completion_tokens=result.usage["completionTokens"],
        artefact_body=result.content,
        detail={"attempts": result.attempts, "amend": bool(state.amend_comments),
                "rework": bool(rework), "ragSnippets": len(snippets)},
    )
    # Record who served this generation; flag deterministic-mock output loudly —
    # it is placeholder content and the usual cause of "hard-coded / drifted" results.
    state.last_provider, state.last_model = result.provider, result.model
    if result.provider == "mock" or "mock" in (result.model or "").lower():
        emit({"type": "node", "node": "guardrail",
              "label": "⚠ Served by the deterministic MOCK provider — output is placeholder. "
                       "Set GENERATION_MODE=llm with a configured provider (Bedrock/Groq/Gemini) for real artifacts."})
    return data


# ---------------------------------------------------------------- Validation agent
def _output_digest(out: BaseModel, *, limit: int = 3_000) -> str:
    """A compact, token-frugal digest of a generated phase output so the
    validation agent can judge it without re-sending the whole payload. Strings
    are truncated; lists are summarised by count + first item's headline."""
    def head(v: Any) -> str:
        if isinstance(v, dict):
            for k in ("title", "name", "id", "level", "code"):
                if v.get(k):
                    return str(v[k])
            return "{…}"
        return str(v)

    lines: list[str] = []
    for field, value in out.model_dump().items():
        if isinstance(value, str):
            text = value.strip().replace("\n", " ")
            lines.append(f"- {field}: {text[:240]}" + ("…" if len(text) > 240 else ""))
        elif isinstance(value, list):
            preview = ", ".join(head(v) for v in value[:5])
            lines.append(f"- {field}: [{len(value)} item(s)] {preview}"[:280])
        elif isinstance(value, dict):
            lines.append(f"- {field}: {{{', '.join(list(value)[:8])}}}")
        elif value is not None:
            lines.append(f"- {field}: {value}")
    digest = "\n".join(lines)
    return digest[:limit] + ("\n…(truncated)" if len(digest) > limit else "")


def _rework_text(verdict: ValidationVerdict) -> str:
    """The modification instructions handed back to the phase agent."""
    if verdict.reworkInstructions.strip():
        return verdict.reworkInstructions.strip()
    return "Fix the following issues while preserving everything already correct:\n" + "\n".join(
        f"- {i.problem}" + (f" — {i.fix}" if i.fix else "") for i in verdict.issues
    )


def _deterministic_quality(state: AgentState, out: BaseModel, phase) -> tuple[list[ValidationIssue], int | None]:  # noqa: ANN001
    """Model-independent quality guards that a weak judge model misses: content
    generated with no grounding input, and mandated requirement sections that must
    be present regardless of the model. Returns (extra issues, score cap)."""
    issues: list[ValidationIssue] = []
    cap: int | None = None

    # No grounding at all → a perfect score is impossible; the scope was inferred.
    no_input = (
        not (state.user_input or "").strip()
        and not (state.extra_context or "").strip()
        and not state.context_window
    )
    if no_input:
        issues.append(ValidationIssue(
            severity="warning", area="grounding",
            problem="Generated with no requirement input and no upstream context — the scope was inferred, not grounded.",
            fix="Provide the actual requirements (or answer the clarifying questions) and regenerate."))
        cap = 60

    # Requirement stage must carry the mandated sections (compliance/legal,
    # confidence, open items) — enforced deterministically so the deepening does
    # not depend on the model choosing to include them.
    if phase.id == 1:
        text = getattr(out, "prdMarkdown", "") or ""
        checks = {
            "Compliance, legal & regulatory": r"complian|regulat|gdpr|pci|hipaa|sox|wcag|data.?privacy|\blegal\b",
            "Requirements Confidence": r"confidence",
            "Assumptions & Open Items": r"open item|open question|assumption",
        }
        missing = [name for name, pat in checks.items() if not re.search(pat, text, re.I)]
        for name in missing:
            issues.append(ValidationIssue(
                severity="warning", area="completeness",
                problem=f"The requirements are missing the mandated '{name}' section.",
                fix=f"Add a '{name}' section to the PRD."))
        if missing:
            cap = min(cap if cap is not None else 100, 68)
    return issues, cap


async def _validate_output(
    deps: AgentDeps, state: AgentState, emit: Emit, out: BaseModel,
) -> ValidationVerdict:
    """The validation agent: deterministic syntax checks + an LLM judgement of the
    output against the user's intent and the phase quality bar. Returns a
    verdict; the caller decides whether to trigger a rework."""
    phase = get_phase(state.stage_template)
    syntactic = syntactic_issues(out.model_dump())
    syntactic_issue_models = [
        ValidationIssue(severity="error", area=field, problem=problem,
                        fix="Correct the syntax so it parses/renders.")
        for field, problem in syntactic
    ]

    context_digest = "\n".join(
        f"- [Phase {a.phase}] {a.type}: {a.title}" for a in state.context_window[-25:]
    ) or "(no upstream context)"

    verdict = ValidationVerdict(ok=True)
    try:
        data, _ = await deps.llm.generate_json(
            intent="standard", tag=f"validation_stage{state.current_phase}",
            temperature=0, max_tokens=1024, schema=ValidationVerdict,
            model=state.model_overrides.get("validate") or None, # per-step model override
            messages=[
                {"role": "system", "content": render_prompt("validate.system")},
                {"role": "user", "content": render_prompt(
                    "validate.user",
                    stage_name=state.stage_name or phase.name,
                    quality_bar=render_prompt(f"phase.quality.{phase.id}")[:1_800],
                    user_intent=state.user_input[:1_500] or "(carry the previous phases forward)",
                    amend_comments=(state.amend_comments or "(none)")[:1_200],
                    output_digest=_output_digest(out),
                    context_digest=context_digest[:2_500],
                    syntax_errors=format_issues(syntactic) or "(none)",
                )},
            ],
        )
        verdict = data
    except Exception as err:  # LLM/verdict failure must not block delivery
        log.warning("validation LLM check failed, using syntax-only verdict: %s", err)

    # Deterministic syntax errors are authoritative: they always count, even if
    # the LLM judged the content OK.
    if syntactic_issue_models:
        existing = {(i.area, i.problem) for i in verdict.issues}
        verdict.issues = syntactic_issue_models + [
            i for i in verdict.issues if (i.area, i.problem) not in existing
        ]
        verdict.ok = False
        if not verdict.reworkInstructions.strip():
            verdict.reworkInstructions = _rework_text(verdict)
    else:
        # Trust the LLM's ok flag, but keep it consistent with its own issues.
        if any(i.severity == "error" for i in verdict.issues):
            verdict.ok = False

    # Model-independent quality guards: no-grounding generation and missing
    # mandated requirement sections. These run regardless of what the judge model
    # said, so a weak judge cannot wave through invented or incomplete output.
    det_issues, det_cap = _deterministic_quality(state, out, phase)
    if det_issues:
        existing = {(i.area, i.problem) for i in verdict.issues}
        verdict.issues += [i for i in det_issues if (i.area, i.problem) not in existing]
    if det_cap is not None:
        verdict.score = min(verdict.score, det_cap)

    # Deterministic-mock output is placeholder content — cap its score and flag it.
    if state.last_provider == "mock" or "mock" in (state.last_model or "").lower():
        verdict.score = min(verdict.score, 25)
        verdict.ok = False
        if not any("mock" in (i.problem or "").lower() for i in verdict.issues):
            verdict.issues.append(ValidationIssue(
                severity="error", area="grounding",
                problem="Generated by the deterministic mock provider (placeholder, not a real model).",
                fix="Run with GENERATION_MODE=llm and a configured provider (Bedrock/Groq/Gemini)."))
    if syntactic_issue_models:
        verdict.score = min(verdict.score, 50)
    # Below the quality floor → flag for the human reviewer (and drive rework).
    floor = getattr(deps.settings, "QUALITY_MIN_SCORE", 70)
    if verdict.score < floor:
        verdict.ok = False
    emit({"type": "node", "node": "validator",
          "label": f"Quality score {verdict.score}/100"
                   + (f" · {verdict.summary}" if verdict.summary else "")
                   + (" · below the quality bar — flagged for review" if verdict.score < floor else "")})
    # Persist the score as a timestamped audit event so the quality-metrics
    # dashboard can trend validator scores per stage over time.
    try:
        deps.audit.record(
            project_id=state.project_id, phase=state.current_phase,
            agent_role=state.stage_name or "ValidationAgent", event="ai.validation",
            provider=state.last_provider, model=state.last_model,
            detail={"score": verdict.score, "dimensions": verdict.dimensions or {},
                    "ok": verdict.ok, "issues": len(verdict.issues), "belowBar": verdict.score < floor},
        )
    except Exception:  # audit must never break the pipeline
        pass
    return verdict


async def _persist_validation_feedback(
    deps: AgentDeps, state: AgentState, verdict: ValidationVerdict,
) -> None:
    """Surface the validation agent's verdict as quality signals on the stage so a
    human sees what the auto-checker caught before sign-off. Best-effort:
    the DB may not carry the table in lean test setups."""
    repl = getattr(deps.db, "replace_validation_feedback", None)
    if repl is None:
        return
    floor = getattr(deps.settings, "QUALITY_MIN_SCORE", 70)
    dims = ", ".join(f"{k} {v}" for k, v in (verdict.dimensions or {}).items())
    # Lead with the overall quality score so the reviewer sees it before the issues.
    score_signal = [{
        "category": "quality-score",
        "severity": "error" if verdict.score < floor else "warning",
        "comment": (f"Quality score {verdict.score}/100"
                    + (f" (below the {floor} bar)" if verdict.score < floor else "")
                    + (f" — {verdict.summary}" if verdict.summary else "")
                    + (f" [{dims}]" if dims else "")).strip(),
    }]
    issues = score_signal + [
        {"category": i.area or "quality", "severity": i.severity,
         "comment": (i.problem + (f" — Fix: {i.fix}" if i.fix else "")).strip()}
        for i in verdict.issues
    ]
    try:
        await repl(project_id=state.project_id, phase=state.current_phase, issues=issues)
    except Exception as err:  # never block delivery on a feedback write
        log.warning("could not persist validation feedback: %s", err)


async def _generate_validated(deps: AgentDeps, state: AgentState, emit: Emit) -> BaseModel:
    """Generate a phase's output, then validate it against the user's intent and
    for syntactic correctness, re-invoking the phase agent with concrete
    modification instructions on failure — bounded by VALIDATION_MAX_REPAIRS so a
    stubborn model can't loop. Best-effort: never blocks delivery."""
    out = await _generate(deps, state, emit)
    if not getattr(deps.settings, "VALIDATION_ENABLED", True):
        return out

    max_repairs = max(0, getattr(deps.settings, "VALIDATION_MAX_REPAIRS", 1))
    for attempt in range(max_repairs + 1):
        emit({"type": "node", "node": "validator",
              "label": "Validator: checking output against your intent and for syntax errors"})
        verdict = await _validate_output(deps, state, emit, out)
        errors = [i for i in verdict.issues if i.severity == "error"]
        deps.audit.record(
            project_id=state.project_id, phase=state.current_phase, agent_role="ValidationAgent",
            event="ai.validation", detail={
                "ok": verdict.ok, "attempt": attempt,
                "errors": [i.problem for i in errors][:10],
                "warnings": [i.problem for i in verdict.issues if i.severity == "warning"][:10],
            },
        )
        if verdict.ok or not errors:
            emit({"type": "node", "node": "validator", "label": "Validator: output validated ✓"})
            await _persist_validation_feedback(deps, state, verdict)
            return out
        if attempt == max_repairs:
            emit({"type": "node", "node": "validator",
                  "label": f"Validator: {len(errors)} issue(s) remain after rework — flagged for gate review"})
            await _persist_validation_feedback(deps, state, verdict)
            return out
        emit({"type": "node", "node": "validator",
              "label": f"Validator: {len(errors)} issue(s) found — asking the agent to rework"})
        out = await _generate(deps, state, emit, rework=_rework_text(verdict))
    return out


# ---------------------------------------------------------------- diagram synth fallback (/52)
def _diag_slug(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_") or "n"
    if base[0].isdigit():
        base = f"n_{base}"
    slug, i = base, 2
    while slug in taken:
        slug, i = f"{base}_{i}", i + 1
    taken.add(slug)
    return slug


def _guess_service(*hints: str) -> str:
    """Map component technology/name hints to a diagram service (icon) key that
    diagram_render understands; defaults to a generic component box."""
    text = " ".join(h.lower() for h in hints if h)
    table = [
        (("cloudfront", "cdn"), "cloudfront"),
        (("waf", "firewall"), "waf"),
        (("load balancer", "alb", "elb", "gateway", "ingress"), "alb"),
        (("aurora",), "aurora"),
        (("rds", "postgres", "mysql", "sql database", "relational"), "rds"),
        (("dynamo", "nosql", "mongo"), "database"),
        (("redis", "cache", "memcached", "elasticache"), "cache"),
        (("s3", "bucket", "object storage", "blob"), "s3"),
        (("sqs", "queue", "kafka", "sns", "event bus", "messaging", "stream"), "sqs"),
        (("lambda", "function", "fargate", "ecs", "container", "docker", "service", "microservice", "api"), "fargate"),
        (("user", "customer", "client", "browser", "actor"), "user"),
        (("database", "db", "datastore", "store"), "database"),
    ]
    for keys, service in table:
        if any(k in text for k in keys):
            return service
    return "component"


def _synth_architecture(components: list[Any], *, title: str, direction: str, deps_attr: str) -> CloudArchitecture | None:
    """Build a CloudArchitecture from a phase's component catalogue when the LLM
    omits the explicit diagram spec ( robustness): one node per component,
    edges from each component's declared dependencies/collaborators. Returns None
    if there is nothing to draw."""
    if not components:
        return None
    taken: set[str] = set()
    by_name: dict[str, str] = {}
    nodes: list[DiagramNode] = []
    for c in components:
        nid = _diag_slug(c.name, taken)
        by_name[c.name.strip().lower()] = nid
        nodes.append(DiagramNode(
            id=nid, label=c.name,
            service=_guess_service(getattr(c, "technology", "") or "", c.name),
        ))
    edges: list[DiagramEdge] = []
    seen: set[tuple[str, str]] = set()
    for c in components:
        src = by_name[c.name.strip().lower()]
        for dep in getattr(c, deps_attr, None) or []:
            tid = by_name.get(str(dep).strip().lower())
            if tid and tid != src and (src, tid) not in seen:
                seen.add((src, tid))
                edges.append(DiagramEdge(fromId=src, toId=tid, label=""))
    return CloudArchitecture(title=title, direction=direction, clusters=[], nodes=nodes, edges=edges)


_TEST_PATH = re.compile(
    r"(^|/)(tests?|spec|__tests__)/|"                 # tests/ src/test/ __tests__/
    r"[._-](test|spec)\.[A-Za-z0-9]+$|"               # foo.test.ts  foo_spec.rb
    r"(^|/)test_[^/]+\.py$|"                          # test_foo.py
    r"Tests?\.(java|kt|cs)$",                         # FooTest.java
    re.IGNORECASE,
)


def _is_test_path(path: str) -> bool:
    """Classify a generated file as test vs production source, so the two are
    traceable separately (phase 6 produces APP_CODE and UNIT_TESTS)."""
    return bool(_TEST_PATH.search(path.replace("\\", "/")))


_JIRA_KEY_FEEDBACK = re.compile(
    r"(?:identifier|project\s*key|prefix|jira\s*key)\b[^.\n]*?\bto\s+['\"]?([A-Za-z][A-Za-z0-9]{1,5})",
    re.IGNORECASE,
)


def _jira_key_from_feedback(comments: str | None) -> str | None:
    """Deterministically pull an explicit Jira identifier out of reviewer amend
    feedback (e.g. 'change the identifier from SDLC to AQDP' -> 'AQDP'), so the
    exact key the reviewer asked for is honoured rather than left to the model."""
    if not comments:
        return None
    m = _JIRA_KEY_FEEDBACK.search(comments)
    return m.group(1).upper() if m else None


def _ctx_content(state: AgentState, type_: str) -> str | None:
    """Newest artifact body of a given type from the accumulated context
    (exact artifacts carry their full content across stages)."""
    for a in reversed(state.context_window):
        if a.type == type_ and a.content:
            return a.content
    return None


async def _tool(deps: AgentDeps, emit: Emit, name: str, args: dict[str, Any]) -> dict[str, Any]:
    emit({"type": "tool_call", "tool": name, "status": "start"})
    started = time.perf_counter()
    try:
        result = await deps.mcp.call(name, args)
    except Exception as err:
        if deps.telemetry is not None: # tool span
            await deps.telemetry.record(
                kind="tool", tag=name, status="error", error=str(err),
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        emit({"type": "tool_call", "tool": name, "status": "error", "summary": str(err)[:200]})
        raise
    if deps.telemetry is not None:
        await deps.telemetry.record(
            kind="tool", tag=name, latency_ms=int((time.perf_counter() - started) * 1000),
        )
    summary = next(
        (f"{k}={result[k]}" for k in
         ("epicKey", "storyKey", "xrayTestKey", "pageId", "prNumber", "commitSha", "runId", "result")
         if k in result),
        "ok",
    )
    emit({"type": "tool_call", "tool": name, "status": "success", "summary": summary})
    return result


def _story_sentence(st: Any) -> str:
    """The canonical one-line 'As a X, I want Y, so that Z.'"""
    s = st.statement
    return f"As a {s.asA}, I want {s.iWant}, so that {s.soThat}."


def _render_story_md(story: Any, story_key: str) -> str:
    """A full user-story card: statement, estimate + rationale, Gherkin ACs and
    the technical sub-task breakdown (traceability + delivery detail)."""
    lines = [
        f"# {story_key}: {story.title}",
        "",
        f"**Priority:** {story.priority}  |  **Story points:** {story.storyPoints}",
        "",
        "## User story",
        f"- **As a** {story.statement.asA}",
        f"- **I want** {story.statement.iWant}",
        f"- **So that** {story.statement.soThat}",
    ]
    if story.estimationRationale:
        lines += ["", "## Estimation rationale", story.estimationRationale]
    lines += ["", "## Acceptance criteria (Gherkin)"]
    for i, ac in enumerate(story.acceptanceCriteria, 1):
        lines += [f"### Scenario {i}", "```gherkin", ac.strip(), "```"]
    if story.subTasks:
        total = sum(t.estimatedHours for t in story.subTasks)
        lines += ["", f"## Technical sub-tasks (≈ {total:g}h)", "", "| # | Task | Est. (h) |", "|---|---|---|"]
        for i, t in enumerate(story.subTasks, 1):
            lines.append(f"| {i} | {t.title} | {t.estimatedHours:g} |")
    return "\n".join(lines)


async def _save_architecture_svg(
    deps: AgentDeps, state: AgentState, emit: Emit, *, spec: Any, type_: str, title: str, summary: str,
) -> ContextArtifact | None:
    """Render a CloudArchitecture spec to a self-contained SVG (real AWS icons +
    nested clusters) and save it as an artifact. Returns None when the spec
    is absent or the renderer is unavailable — generation continues either way."""
    if spec is None:
        return None
    svg = await asyncio.to_thread(render_architecture, spec.model_dump())
    if not svg:
        return None
    return await _save_artifact(
        deps, state, emit, type_=type_, title=title, content=svg, summary=summary, exact=True,
    )


async def _save_architecture_drawio(
    deps: AgentDeps, state: AgentState, emit: Emit, *, spec: Any, title: str, summary: str,
) -> ContextArtifact | None:
    """Editable draw.io derived DETERMINISTICALLY from the same
    CloudArchitecture spec that produced the SVG — so the architect gets a
    professional, fully editable diagram at NO extra model cost. Validated before
    saving; a spec-less or invalid result is skipped, generation continues."""
    if spec is None:
        return None
    from ..services.drawio import cloud_arch_to_drawio, validate_drawio
    try:
        xml = cloud_arch_to_drawio(spec.model_dump(), title=title)
        verdict = validate_drawio(xml)
        if not verdict["ok"]:
            log.warning("generated draw.io failed validation: %s", verdict["errors"][:3])
            return None
    except Exception as err:  # noqa: BLE001 — diagram export is best-effort
        log.warning("draw.io export failed: %s", err)
        return None
    return await _save_artifact(
        deps, state, emit, type_="DRAWIO", title=title, content=xml, summary=summary, exact=True,
    )


def _hld_structured(out: Any) -> str:
    """Append first-class architecture structure to the HLD narrative:
    principles, component catalogue, design patterns and quantified NFRs."""
    parts: list[str] = []
    if out.architecturePrinciples:
        parts += ["", "## Architecture principles", *[f"- {p}" for p in out.architecturePrinciples]]
    if out.components:
        parts += ["", "## Component catalogue", "", "| Component | Responsibility | Technology | Depends on |",
                  "|---|---|---|---|"]
        for c in out.components:
            parts.append(f"| {c.name} | {c.responsibility} | {c.technology or '—'} | {', '.join(c.dependsOn) or '—'} |")
    if out.designPatterns:
        parts += ["", "## Design patterns applied", "", "| Pattern | Applied to | Rationale |", "|---|---|---|"]
        for p in out.designPatterns:
            parts.append(f"| {p.name} | {p.appliedTo} | {p.rationale} |")
    if out.qualityAttributes:
        parts += ["", "## Quality attributes (NFRs)", "", "| ID | Attribute | Target | Tactic |", "|---|---|---|---|"]
        for q in out.qualityAttributes:
            parts.append(f"| {q.id} | {q.attribute} | {q.target} | {q.tactic or '—'} |")
    return "\n".join(parts)


def _lld_structured(out: Any) -> str:
    """Append component responsibilities, the error taxonomy and concrete
    resilience settings to the LLD narrative."""
    parts: list[str] = []
    if out.components:
        parts += ["", "## Component responsibilities", "", "| Component | Responsibility | Collaborators |",
                  "|---|---|---|"]
        for c in out.components:
            parts.append(f"| {c.name} | {c.responsibility} | {', '.join(c.collaborators) or '—'} |")
    if out.errorTaxonomy:
        parts += ["", "## Error taxonomy", "", "| Code | HTTP | Message | Retryable |", "|---|---|---|---|"]
        for e in out.errorTaxonomy:
            parts.append(f"| {e.code} | {e.httpStatus} | {e.message} | {'yes' if e.retryable else 'no'} |")
    if out.resilience:
        r = out.resilience
        ttl = f"{r.cacheTtlSeconds}s" if r.cacheTtlSeconds is not None else "n/a"
        parts += ["", "## Resilience settings",
                  f"- Retries: {r.retries}", f"- Timeout: {r.timeoutMs} ms",
                  f"- Circuit breaker: {r.circuitBreaker or 'n/a'}", f"- Cache TTL: {ttl}"]
    return "\n".join(parts)


def _test_strategy_structured(out: Any) -> str:
    """Append the test pyramid, risk-based priorities, entry/exit criteria and
    defect SLAs to the test strategy."""
    parts: list[str] = []
    if out.testLevels:
        parts += ["", "## Test levels (pyramid)", "", "| Level | Scope | Coverage target | Tools |",
                  "|---|---|---|---|"]
        for l in out.testLevels:
            parts.append(f"| {l.level} | {l.scope} | {l.coverageTarget or '—'} | {', '.join(l.tools) or '—'} |")
    if out.riskAreas:
        parts += ["", "## Risk-based prioritisation", "", "| Area | Likelihood | Impact | Priority | Mitigation |",
                  "|---|---|---|---|---|"]
        for r in out.riskAreas:
            parts.append(f"| {r.area} | {r.likelihood} | {r.impact} | {r.priority} | {r.mitigation or '—'} |")
    if out.entryCriteria:
        parts += ["", "## Entry criteria", *[f"- {c}" for c in out.entryCriteria]]
    if out.exitCriteria:
        parts += ["", "## Exit criteria", *[f"- {c}" for c in out.exitCriteria]]
    if out.defectSlas:
        parts += ["", "## Defect SLAs", "", "| Severity | Triage | Resolution |", "|---|---|---|"]
        for d in out.defectSlas:
            parts.append(f"| {d.severity} | {d.triage} | {d.resolution} |")
    return "\n".join(parts)


def _pipeline_design_markdown(out: Any) -> str:
    """A CI/CD & operations design doc from the structured pipeline fields."""
    parts = ["# CI/CD & Operations design", ""]
    if out.pipelineStages:
        parts += ["## Pipeline stages", "", "| Stage | Purpose | Gate | Tools |", "|---|---|---|---|"]
        for s in out.pipelineStages:
            parts.append(f"| {s.name} | {s.purpose} | {'blocking' if s.blocking else 'non-blocking'} | {', '.join(s.tools) or '—'} |")
    if out.securityGates:
        parts += ["", "## Security gates (shift-left)", *[f"- {g}" for g in out.securityGates]]
    if out.observabilitySlos:
        parts += ["", "## Observability SLOs & alerts", "", "| SLO | Target | Alert threshold |", "|---|---|---|"]
        for s in out.observabilitySlos:
            parts.append(f"| {s.name} | {s.target} | {s.alertThreshold or '—'} |")
    if out.rolloutStrategy:
        parts += ["", "## Rollout strategy", out.rolloutStrategy]
    if out.rollback:
        parts += ["", "## Rollback", out.rollback]
    return "\n".join(parts)


# ---------------------------------------------------------------- Phase 1: PO
async def _run_phase1(deps: AgentDeps, state: AgentState, emit: Emit) -> PhaseAgentResult:
    out: Phase1Output = await _generate_validated(deps, state, emit)  # type: ignore[assignment]
    artifacts: list[ContextArtifact] = []
    jira_links: list[str] = []
    story_count = 0
    feature_count = 0

    # Project-specific Jira key: sanitise the model's choice (uppercase
    # alphanumerics, 2–6 chars); empty/invalid falls back to the connector
    # default. When the reviewer explicitly names a new identifier in their amend
    # feedback, honour it DETERMINISTICALLY — don't rely on the model to echo the
    # exact key (weaker models invent their own). That is what makes feedback
    # like "change the identifier from SDLC to AQDP" reliably take effect.
    chosen_key = _jira_key_from_feedback(state.amend_comments) or out.jiraProjectKey or ""
    proj_key = re.sub(r"[^A-Z0-9]", "", chosen_key.upper())
    key_arg = {"projectKey": proj_key} if 2 <= len(proj_key) <= 6 else {}

    for epic in out.epics:
        created = await _publish(deps, emit, "jira_create_epic",
                                 {"title": epic.title, "description": epic.businessCase,
                                  "priority": epic.priority, **key_arg})
        jira_links.append(created["epicKey"])
        epic_md = "\n".join([
            f"# {created['epicKey']}: {epic.title}",
            "",
            f"**Priority:** {epic.priority}"
            + (f"  |  **Target:** {epic.targetQuarter}" if epic.targetQuarter else ""),
            "",
            "## Business case",
            epic.businessCase,
            *(["", "## Success metric", epic.successMetric] if epic.successMetric else []),
            "",
            "## Features",
            *[f"- **{f.title}** ({f.priority}) — {f.description}" for f in epic.features],
        ])
        artifacts.append(await _save_artifact(
            deps, state, emit, type_="EPIC", title=f"{created['epicKey']}: {epic.title}",
            content=epic_md, url=created["url"], ref_key=created["epicKey"],
            summary=f"Epic {created['epicKey']} — {epic.title}: {epic.businessCase[:150]}",
        ))

        for feature in epic.features:
            feature_count += 1
            fkey = f"{created['epicKey']}-F{feature_count}"
            feature_md = "\n".join([
                f"# Feature {fkey}: {feature.title}",
                "",
                f"**Parent epic:** {created['epicKey']}  |  **Priority:** {feature.priority}",
                "",
                "## Description",
                feature.description,
                *(["", "## Acceptance criteria", *[f"- {c}" for c in feature.acceptanceCriteria]]
                  if feature.acceptanceCriteria else []),
                "",
                "## Stories",
                *[f"- {_story_sentence(s)} ({s.storyPoints} pts)" for s in feature.stories],
            ])
            artifacts.append(await _save_artifact(
                deps, state, emit, type_="FEATURE", title=f"{fkey}: {feature.title}",
                content=feature_md, ref_key=fkey,
                summary=f"Feature {fkey} — {feature.title}: {feature.description[:150]}",
            ))

            for story in feature.stories:
                story_count += 1
                s = await _publish(deps, emit, "jira_create_story", {
                    "epicKey": created["epicKey"], "storyText": _story_sentence(story),
                    "gherkinCriteria": story.acceptanceCriteria, "storyPoints": story.storyPoints,
                    **key_arg,
                })
                jira_links.append(s["storyKey"])
                artifacts.append(await _save_artifact(
                    deps, state, emit, type_="USER_STORY",
                    title=f"{s['storyKey']}: {story.title}",
                    content=_render_story_md(story, s["storyKey"]),
                    url=s["url"], ref_key=s["storyKey"],
                    summary=f"{s['storyKey']} ({story.storyPoints}pts) — {_story_sentence(story)[:140]}",
                ))

    # Definition of Ready / Done appended so the PRD is a governance-complete doc.
    prd_body = out.prdMarkdown
    if out.definitionOfReady:
        prd_body += "\n\n## Definition of Ready\n" + "\n".join(f"- {i}" for i in out.definitionOfReady)
    if out.definitionOfDone:
        prd_body += "\n\n## Definition of Done\n" + "\n".join(f"- {i}" for i in out.definitionOfDone)

    prd = await _publish(deps, emit, "confluence_publish_prd", {
        "title": f"PRD — {state.user_input[:60]}", "content": prd_body, "jiraLinks": jira_links,
    })
    artifacts.append(await _save_artifact(
        deps, state, emit, type_="PRD", title="Product Requirements Document",
        content=prd_body, url=prd["url"], summary=prd_body[:300],
    ))
    return PhaseAgentResult(
        summary=f"Phase 1 complete: {len(out.epics)} epic(s), {feature_count} feature(s), {story_count} "
                f"INVEST user stories with Gherkin ACs and sub-task breakdowns prepared, plus the PRD "
                f"(with DoR/DoD). Jira tickets and the Confluence page publish once the gate is approved.",
        new_artifacts=artifacts, gate_status="PENDING_REVIEW",
    )


# ---------------------------------------------------------------- Phase 2: SA
async def _run_phase2(deps: AgentDeps, state: AgentState, emit: Emit) -> PhaseAgentResult:
    out: Phase2Output = await _generate_validated(deps, state, emit)  # type: ignore[assignment]
    artifacts: list[ContextArtifact] = []

    cloudcraft = await _tool(deps, emit, "amazonq_generate_cloudcraft", {"hldNarrative": out.hldNarrative})
    commit = await _publish(deps, emit, "github_commit_diagrams", {
        "branch": "main",
        "files": [
            {"path": "docs/architecture/workspace.dsl", "content": out.structurizrDsl},
            {"path": "docs/architecture/cloudcraft.json", "content": cloudcraft["cloudcraftJson"]},
        ],
        "message": "docs(architecture): add Structurizr + Cloudcraft topology (Phase 02)",
    })
    page = await _publish(deps, emit, "confluence_publish_hld", {
        "title": f"HLD — {state.user_input[:60]}", "hldContent": out.hldNarrative,
        "structurizrDsl": out.structurizrDsl, "adrLinks": [a.title for a in out.adrs],
    })

    hld_body = out.hldNarrative + _hld_structured(out)
    artifacts.append(await _save_artifact(
        deps, state, emit, type_="HLD", title="High-Level Design",
        content=hld_body, url=page["url"], summary=out.hldNarrative[:300],
    ))
    artifacts.append(await _save_artifact(
        deps, state, emit, type_="STRUCTURIZR_DSL", title="C4 model (Structurizr DSL)",
        content=out.structurizrDsl, url=commit["htmlUrl"],
        summary="C4 container model committed to git", exact=True,
    ))
    artifacts.append(await _save_artifact(
        deps, state, emit, type_="HLD_DIAGRAM", title="Architecture diagram (Mermaid)",
        content=out.mermaidArchitecture, summary="Rendered architecture diagram", exact=True,
    ))
    # Professional AWS deployment diagram: real icons + nested clusters.
    # If the model omitted the explicit spec, synthesise one from the component
    # catalogue so the HLD always carries a rendered architecture diagram.
    arch_spec = out.deploymentArchitecture or _synth_architecture(
        out.components, title="Deployment architecture", direction="TB", deps_attr="dependsOn",
    )
    svg = await _save_architecture_svg(
        deps, state, emit, spec=arch_spec, type_="ARCH_DIAGRAM",
        title="Deployment architecture (AWS)", summary="AWS deployment diagram with VPC/subnet clusters",
    )
    if svg:
        artifacts.append(svg)
    # Editable draw.io of the same topology — professional + fully editable.
    dio = await _save_architecture_drawio(
        deps, state, emit, spec=arch_spec, title="Deployment architecture (draw.io)",
        summary="Editable draw.io AWS deployment diagram",
    )
    if dio:
        artifacts.append(dio)
    artifacts.append(await _save_artifact(
        deps, state, emit, type_="CLOUDCRAFT_JSON", title="AWS topology (Cloudcraft)",
        content=cloudcraft["cloudcraftJson"], url=commit["htmlUrl"], summary="Cloudcraft AWS topology JSON",
    ))
    for adr in out.adrs:
        # Full ADR form: the rejected alternatives are what make it a
        # decision record rather than an assertion.
        options = (
            "\n\n## Options considered\n"
            + "\n".join(f"- {o}" for o in adr.optionsConsidered)
            if adr.optionsConsidered else ""
        )
        artifacts.append(await _save_artifact(
            deps, state, emit, type_="ADR", title=adr.title,
            content=f"# {adr.title}\n\n**Status:** {adr.status}\n\n## Context\n{adr.context}"
                    f"{options}\n\n## Decision\n{adr.decision}\n\n## Consequences\n{adr.consequences}",
            summary=f"{adr.title}: {adr.decision[:140]}",
        ))
    return PhaseAgentResult(
        summary=f"Phase 2 complete: HLD + C4 Structurizr model + Cloudcraft topology generated and "
                f"{len(out.adrs)} ADRs recorded. The diagram commit to GitHub and the HLD Confluence "
                f"page publish once the gate is approved.",
        new_artifacts=artifacts, gate_status="PENDING_REVIEW",
    )


# ---------------------------------------------------------------- Phase 3: TA
async def _run_phase3(deps: AgentDeps, state: AgentState, emit: Emit) -> PhaseAgentResult:
    out: Phase3Output = await _generate_validated(deps, state, emit)  # type: ignore[assignment]
    artifacts: list[ContextArtifact] = []

    # Mandatory Spectral gate with auto-correct loop (Module 5, Phase 03).
    openapi_yaml = out.openapiYaml
    for attempt in range(3):
        lint = await _tool(deps, emit, "spectral_lint_openapi", {"openapiYaml": openapi_yaml})
        errors = [v for v in lint["violations"] if v["severity"] == "error"]
        if lint["result"] == "PASS" or not errors:
            break
        if attempt == 2:
            raise RuntimeError(f"OpenAPI failed lint after auto-correct attempts: {[v['code'] for v in errors]}")
        emit({"type": "node", "node": "agent", "label": f"Spectral FAIL ({len(errors)} errors) — auto-correcting"})
        system, user = openapi_fix_prompt(openapi_yaml, errors)
        fix, _ = await deps.llm.generate_json(
            intent="generation", tag="phase3_openapi_fix", schema=OpenapiFix, temperature=0,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        openapi_yaml = fix.openapiYaml

    commit = await _publish(deps, emit, "github_commit_lld_artefacts", {
        "branch": "main",
        "files": [
            *[{"path": f"docs/design/diagram-{i + 1}.puml", "content": d} for i, d in enumerate(out.plantumlDiagrams)],
            {"path": "docs/design/openapi.yaml", "content": openapi_yaml},
            {"path": "docs/design/schema.dbml", "content": out.dbmlSchema},
            {"path": "infra/cdk/service-stack.ts", "content": out.cdkStack},
        ],
        "message": "docs(design): add LLD artefacts (Phase 03)",
    })
    page = await _publish(deps, emit, "confluence_publish_lld", {
        "serviceName": state.user_input[:60], "lldContent": out.lldMarkdown,
        "plantumlSources": out.plantumlDiagrams, "openapiYaml": openapi_yaml,
    })

    artifacts.append(await _save_artifact(
        deps, state, emit, type_="LLD", title="Low-Level Design",
        content=out.lldMarkdown + _lld_structured(out),
        url=page["url"], summary=out.lldMarkdown[:300], exact=True,
    ))
    # Professional component/deployment diagram. Fall back to synthesising
    # from the component catalogue + collaborators when the model omits the spec,
    # so the LLD reliably ships a rendered component diagram.
    comp_spec = out.componentDiagram or _synth_architecture(
        out.components, title="Component diagram", direction="LR", deps_attr="collaborators",
    )
    svg = await _save_architecture_svg(
        deps, state, emit, spec=comp_spec, type_="COMPONENT_DIAGRAM",
        title="Component diagram (AWS)", summary="Detailed component/deployment diagram",
    )
    if svg:
        artifacts.append(svg)
    # Editable draw.io of the component view — zero extra model cost.
    dio = await _save_architecture_drawio(
        deps, state, emit, spec=comp_spec, title="Component diagram (draw.io)",
        summary="Editable draw.io component diagram",
    )
    if dio:
        artifacts.append(dio)
    artifacts.append(await _save_artifact(
        deps, state, emit, type_="LLD_DIAGRAM", title="Sequence diagram (Mermaid)",
        content=out.mermaidSequence, summary="Rendered primary-flow sequence diagram", exact=True,
    ))
    artifacts.append(await _save_artifact(
        deps, state, emit, type_="OPENAPI", title="OpenAPI 3.0 contract", content=openapi_yaml,
        url=commit["htmlUrl"], summary="Linted OpenAPI 3.0 service contract", exact=True,
    ))
    artifacts.append(await _save_artifact(
        deps, state, emit, type_="DBML", title="Database schema (DBML)", content=out.dbmlSchema,
        url=commit["htmlUrl"], summary="DBML entity model", exact=True,
    ))
    artifacts.append(await _save_artifact(
        deps, state, emit, type_="CDK", title="AWS CDK stack", content=out.cdkStack,
        url=commit["htmlUrl"], summary="CDK IaC stack (TypeScript)",
    ))
    for i, diagram in enumerate(out.plantumlDiagrams):
        artifacts.append(await _save_artifact(
            deps, state, emit, type_="PLANTUML", title=f"PlantUML diagram {i + 1}",
            content=diagram, url=commit["htmlUrl"], summary=f"PlantUML source {i + 1}",
        ))
    return PhaseAgentResult(
        summary=f"Phase 3 complete: LLD, {len(out.plantumlDiagrams)} PlantUML diagram(s), OpenAPI (lint PASS), "
                f"DBML and CDK generated. The GitHub commit and the LLD Confluence page publish once the "
                f"gate is approved.",
        new_artifacts=artifacts, gate_status="PENDING_REVIEW",
    )


# ---------------------------------------------------------------- Phase 4: QA
async def _run_phase4(deps: AgentDeps, state: AgentState, emit: Emit) -> PhaseAgentResult:
    out: Phase4Output = await _generate_validated(deps, state, emit)  # type: ignore[assignment]
    artifacts: list[ContextArtifact] = []

    story_keys = [a.ref.key for a in state.context_window if a.type == "USER_STORY" and a.ref and a.ref.key]
    fallback = story_keys[0] if story_keys else "UNLINKED"

    xray_keys: list[str] = []
    for i, test in enumerate(out.xrayTests):
        created = await _publish(deps, emit, "jira_create_xray_test", {
            "storyKey": story_keys[i % len(story_keys)] if story_keys else fallback,
            "title": test.title,
            "steps": [s.model_dump() for s in test.steps],
        })
        xray_keys.append(created["xrayTestKey"])

    test_strategy = out.testStrategyMarkdown + _test_strategy_structured(out)
    specs = [
        ("TEST_STRATEGY", "Test Strategy", test_strategy, out.testStrategyMarkdown[:300], False),
        ("XRAY_TESTS", f"Xray test cases ({', '.join(xray_keys)})",
         "\n\n".join(t.model_dump_json(indent=2) for t in out.xrayTests),
         f"{len(xray_keys)} Xray tests: {', '.join(xray_keys)}", False),
        ("K6_SCRIPT", "k6 performance script", out.k6Script, "k6 load profile with thresholds", True),
        # exact=True: the raw collection JSON must reach phase 6 for the newman run
        ("POSTMAN_COLLECTION", "Postman collection", out.postmanCollection, "Postman API collection", True),
        ("RTM", "Requirements Traceability Matrix", out.rtmMarkdown, "RTM linking stories to tests", False),
    ]
    for type_, title, content, summary, exact in specs:
        artifacts.append(await _save_artifact(
            deps, state, emit, type_=type_, title=title, content=content, summary=summary, exact=exact,
        ))

    # SDLC toolchain: derive executable suites for every testing layer
    # from the approved contract + stories — REST Assured (API), Playwright
    # (UI), JMeter + Locust (performance). k6 is already generated above.
    service = state.user_input[:60] or "Service"
    openapi = _ctx_content(state, "OPENAPI") or "openapi: 3.0.3\npaths:\n  /healthz:\n    get:\n      summary: health\n"

    ra = await _tool(deps, emit, "restassured_generate_tests", {"openapiYaml": openapi, "serviceName": service})
    artifacts.append(await _save_artifact(
        deps, state, emit, type_="REST_ASSURED", title="REST Assured API tests (Java)",
        content=ra["javaClass"], summary=f"{ra['testCount']} REST Assured tests for {ra['path']}", exact=True,
    ))

    stories = [
        {"key": a.ref.key or f"S{n + 1}", "text": a.title.split(": ", 1)[-1], "criteria": []}
        for n, a in enumerate(state.context_window)
        if a.type == "USER_STORY" and a.ref
    ] or [
        {"key": f"XT-{n + 1}", "text": t.title, "criteria": [s.action for s in t.steps][:3]}
        for n, t in enumerate(out.xrayTests)
    ]
    pw = await _tool(deps, emit, "playwright_generate_tests", {"stories": stories})
    artifacts.append(await _save_artifact(
        deps, state, emit, type_="PLAYWRIGHT_SPEC", title="Playwright UI tests",
        content=pw["specTs"], summary=f"{pw['testCount']} Playwright tests from user stories", exact=True,
    ))

    jm = await _tool(deps, emit, "jmeter_generate_plan", {"openapiYaml": openapi, "serviceName": service})
    artifacts.append(await _save_artifact(
        deps, state, emit, type_="JMETER_PLAN", title="JMeter load-test plan",
        content=jm["jmxXml"], summary=f"JMX plan with {jm['samplerCount']} samplers",
    ))

    lo = await _tool(deps, emit, "locust_generate_test", {"openapiYaml": openapi, "serviceName": service})
    artifacts.append(await _save_artifact(
        deps, state, emit, type_="LOCUSTFILE", title="Locust load-test file",
        content=lo["locustfile"], summary=f"locustfile.py with {lo['taskCount']} tasks",
    ))

    return PhaseAgentResult(
        summary=f"Phase 4 complete: Test Strategy, {len(xray_keys)} Xray test cases, k6 script, "
                "Postman collection, RTM, REST Assured suite, Playwright spec, JMeter plan and "
                "Locust file generated.",
        new_artifacts=artifacts, gate_status="PENDING_REVIEW",
    )


# ---------------------------------------------------------------- Phase 5: DevOps
async def _run_phase5(deps: AgentDeps, state: AgentState, emit: Emit) -> PhaseAgentResult:
    out: Phase5Output = await _generate_validated(deps, state, emit)  # type: ignore[assignment]
    artifacts: list[ContextArtifact] = []

    commit = await _publish(deps, emit, "github_commit_pipeline_config", {
        "branch": "main",
        "files": [
            {"path": ".github/workflows/ci.yml", "content": out.workflowYaml},
            *[f.model_dump() for f in out.dockerfiles],
            {"path": "observability/grafana-dashboard.json", "content": out.grafanaDashboardJson},
        ],
        "message": "ci: add pipeline, Dockerfiles and Grafana dashboard (Phase 05)",
    })

    required = ["checkout", "lint", "build", "test", "snyk", "inspector", "deploy"]
    missing = [s for s in required if s not in out.workflowYaml.lower()]
    emit({"type": "node", "node": "agent",
          "label": "Pipeline dry-run: all 7 stages present" if not missing
          else f"Pipeline dry-run warning: missing {', '.join(missing)}"})

    artifacts.append(await _save_artifact(
        deps, state, emit, type_="GITHUB_ACTIONS", title="CI/CD workflow (7 stages)",
        content=out.workflowYaml, url=commit["htmlUrl"],
        summary="GitHub Actions: checkout, lint, build, test, snyk-scan, inspector-scan, deploy", exact=True,
    ))
    artifacts.append(await _save_artifact(
        deps, state, emit, type_="GRAFANA_DASHBOARD", title="Grafana dashboard",
        content=out.grafanaDashboardJson, url=commit["htmlUrl"], summary="Service health dashboard JSON",
    ))
    # Structured CI/CD & operations design: pipeline stages + gates,
    # security gates, observability SLOs and rollout/rollback as a review doc.
    pipeline_design = _pipeline_design_markdown(out)
    if pipeline_design.strip() and pipeline_design != "# CI/CD & Operations design\n":
        artifacts.append(await _save_artifact(
            deps, state, emit, type_="PIPELINE_DESIGN", title="CI/CD & operations design",
            content=pipeline_design, summary="Pipeline stages, security gates, SLOs and rollout strategy",
        ))
    for dockerfile in out.dockerfiles:
        artifacts.append(await _save_artifact(
            deps, state, emit, type_="DOCKERFILE", title=dockerfile.path,
            content=dockerfile.content, url=commit["htmlUrl"], summary=f"Container build for {dockerfile.path}",
        ))

    # SDLC toolchain: shift-left security — Trivy scans the container
    # image and Secrets Manager is verified to hold the pipeline's secrets
    # (existence only; values never enter the pipeline).
    trivy = await _tool(deps, emit, "trivy_scan_image", {
        "dockerfile": out.dockerfiles[0].content if out.dockerfiles else "FROM scratch",
        "imageTag": "app:candidate",
    })
    secret_rows = []
    for secret_id in ("sdlc/jwt-secret", "sdlc/github-webhook-secret"):
        check = await _tool(deps, emit, "aws_secrets_check", {"secretId": secret_id})
        secret_rows.append(f"| {secret_id} | {'✅ present' if check['exists'] else '❌ MISSING'} | {check['mode']} |")
    artifacts.append(await _save_artifact(
        deps, state, emit, type_="SECURITY_SCAN", title="Pipeline security scan (Trivy + Secrets)",
        content=f"{trivy['reportMarkdown']}\n\n## Secrets Manager verification\n\n"
                f"| Secret | Status | Mode |\n|---|---|---|\n" + "\n".join(secret_rows),
        summary=f"Trivy {trivy['result']}: {trivy['critical']} critical / {trivy['high']} high; "
                f"{len(secret_rows)} pipeline secrets verified",
    ))

    stage_note = "7/7 stages" if not missing else f"missing: {', '.join(missing)}"
    return PhaseAgentResult(
        summary=f"Phase 5 complete: CI/CD workflow ({stage_note}), {len(out.dockerfiles)} Dockerfile(s) and "
                f"Grafana dashboard generated. Trivy image scan {trivy['result']}; Secrets Manager verified. "
                f"Dry-run {'passed' if not missing else 'flagged warnings'}. The GitHub commit publishes once "
                f"the gate is approved.",
        new_artifacts=artifacts, gate_status="PENDING_REVIEW",
    )


# ---------------------------------------------------------------- Phase 6: Dev
async def _run_phase6(deps: AgentDeps, state: AgentState, emit: Emit) -> PhaseAgentResult:
    out: Phase6Output = await _generate_validated(deps, state, emit)  # type: ignore[assignment]
    artifacts: list[ContextArtifact] = []

    # Deterministic quality-gate config: the coverage + linter/formatter
    # config files are pure boilerplate parameterised by (stack, threshold), so
    # the platform writes them in code rather than spending model tokens on them —
    # and the coverage threshold is guaranteed to equal the configured gate. These
    # paths are authoritative: any same-path file the model emitted is replaced.
    if getattr(deps.settings, "QUALITY_GATE_ENABLED", True):
        det = quality_gate_files(
            state.tech_stack,
            getattr(deps.settings, "COVERAGE_MIN_PERCENT", 80),
            getattr(deps.settings, "LINT_REQUIRED", True),
        )
        if det:
            det_paths = {d["path"].lower() for d in det}
            kept = [f for f in out.files if f.path.lower() not in det_paths]
            out.files = kept + [FileEntry(path=d["path"], content=d["content"]) for d in det]
            emit({"type": "node", "node": "agent",
                  "label": f"Added {len(det)} deterministic quality-gate config file(s) "
                           f"(coverage {getattr(deps.settings, 'COVERAGE_MIN_PERCENT', 80)}% + lint) — no tokens spent"})

    await _tool(deps, emit, "github_create_branch", {"branch": out.branch, "from": "main"})
    push = await _tool(deps, emit, "github_commit_code", {
        "branch": out.branch,
        "files": [f.model_dump() for f in out.files],
        "message": out.commitMessage,
    })
    # Each generated file is persisted as a REAL file at its repository-relative
    # path with its own extension (not one concatenated blob), so the Files
    # explorer shows the actual project structure and the viewer can highlight
    # each file by language. Tests are typed UNIT_TESTS so they are traceable.
    for f in out.files:
        is_test = _is_test_path(f.path)
        artifacts.append(await _save_artifact(
            deps, state, emit,
            type_="UNIT_TESTS" if is_test else "APP_CODE",
            title=f.path,
            content=f.content,
            url=push["htmlUrl"],
            summary=f"{'Test' if is_test else 'Source'} file {f.path} on {out.branch}",
            exact=True,
            source_path=f.path,
        ))
    emit({"type": "node", "node": "agent",
          "label": f"{len(out.files)} source file(s) written to {out.branch} with project structure"})
    emit({"type": "node", "node": "agent",
          "label": f"CI pipeline triggered (run {push['runId']}) — Build Recovery Loop engaged"})

    # Surface the engineering reasoning in the PR body: the design/
    # patterns applied, the coding standards followed and the security controls
    # considered — so the reviewer sees the WHY, not just the diff.
    pr_body = out.prBody
    if out.designNotes:
        pr_body += f"\n\n## Design & patterns\n{out.designNotes}"
    if out.codingStandards:
        pr_body += "\n\n## Coding standards applied\n" + "\n".join(f"- {s}" for s in out.codingStandards)
    if out.securityNotes:
        pr_body += "\n\n## Security controls\n" + "\n".join(f"- {s}" for s in out.securityNotes)

    loop = await deps.monitor.start_tracking(
        run_id=push["runId"], project_id=state.project_id, branch=out.branch,
        pr_title=out.prTitle, pr_body=pr_body, checklist=out.checklist, emit=emit,
        stage_seq=state.current_phase, reviewer_role=state.stage_reviewer,
    )
    if loop["state"] == "SUCCEEDED":
        recovered = (f"recovered after {loop['iterations']} AI fix iteration(s)"
                     if loop["iterations"] > 0 else "passed first time")

        # SDLC toolchain: post-CI verification battery — API tests
        # (Postman/newman), UI tests (Playwright), performance (k6), security
        # (OWASP ZAP) and code quality (SonarQube) — folded into two reports.
        sections: list[str] = []
        verdicts: list[str] = []

        postman_json = _ctx_content(state, "POSTMAN_COLLECTION")
        if postman_json:
            pm = await _tool(deps, emit, "postman_run_collection", {"collectionJson": postman_json})
            sections.append(pm["reportMarkdown"])
            verdicts.append(f"API {pm['passed']}/{pm['total']}")
        spec = _ctx_content(state, "PLAYWRIGHT_SPEC")
        if spec:
            pw = await _tool(deps, emit, "playwright_run_tests", {"specTs": spec})
            sections.append(pw["reportMarkdown"])
            verdicts.append(f"UI {pw['passed']}/{pw['total']}")
        k6_script = _ctx_content(state, "K6_SCRIPT")
        if k6_script:
            k6 = await _tool(deps, emit, "k6_run_test", {"script": k6_script})
            sections.append(k6["reportMarkdown"])
            verdicts.append(f"perf p95={k6['p95Ms']}ms {'✅' if k6['thresholdsPassed'] else '❌'}")
        zap = await _tool(deps, emit, "zap_baseline_scan", {
            "targetUrl": "http://staging.sdlc.local",
            **({"openapiYaml": _ctx_content(state, "OPENAPI")} if _ctx_content(state, "OPENAPI") else {}),
        })
        sections.append(zap["reportMarkdown"])
        verdicts.append(f"ZAP {zap['result']}")
        if sections:
            artifacts.append(await _save_artifact(
                deps, state, emit, type_="TEST_EXECUTION_REPORT", title="Test execution report",
                content="\n\n---\n\n".join(sections), summary=" · ".join(verdicts),
            ))

        sonar = await _tool(deps, emit, "sonarqube_analyse", {
            "files": [f.model_dump() for f in out.files],
        })
        artifacts.append(await _save_artifact(
            deps, state, emit, type_="QUALITY_REPORT", title="SonarQube quality report",
            content=sonar["reportMarkdown"],
            summary=f"Quality gate {sonar['qualityGate']}: {sonar['bugs']} bugs, "
                    f"{sonar['codeSmells']} smells, {sonar['coveragePct']}% coverage",
        ))

        return PhaseAgentResult(
            summary=f"Phase 6 complete: code + unit tests pushed to {out.branch}; CI {recovered}; "
                    f"PR opened: {loop.get('prUrl', '')}. Verification battery: {' · '.join(verdicts)}; "
                    f"quality gate {sonar['qualityGate']}.",
            new_artifacts=artifacts, gate_status="PENDING_REVIEW",
        )
    if loop["state"] == "ESCALATED":
        return PhaseAgentResult(
            summary=f"Phase 6: CI failed after {loop['iterations']} automated fix attempts — escalated to a "
                    "human developer (Build Recovery Loop limit reached).",
            new_artifacts=artifacts, gate_status="ESCALATED",
        )
    return PhaseAgentResult(
        summary=f"Phase 6: code pushed to {out.branch}; CI run {push['runId']} in progress — the Build "
                f"Recovery Loop will fix failures automatically (max "
                f"{deps.settings.BUILD_LOOP_MAX_ITERATIONS} iterations) and open the PR on success.",
        new_artifacts=artifacts, gate_status="IN_PROGRESS",
    )


# ---------------------------------------------------------------- Custom phase (, template 7)
async def _run_custom(deps: AgentDeps, state: AgentState, emit: Emit) -> PhaseAgentResult:
    """Generic, PM-configured phase engine (workflow v2). Generates a professional
    Markdown deliverable for a phase type defined entirely by config — persona,
    an optional extra instruction prompt, and the declared output types — then
    opens the gate like any built-in phase. Reuses the same context assembly,
    guardrails, model-override, persistence and audit machinery."""
    persona = state.custom_persona or "Specialist"
    outputs = state.custom_outputs or ["DELIVERABLE"]
    emit({"type": "node", "node": "agent",
          "label": f"{persona} generating for '{state.stage_name}' ({', '.join(outputs)})"})

    context_block, compressed = await build_context_block(
        state.context_window, deps.settings.CONTEXT_TOKEN_THRESHOLD, deps.llm
    )
    if compressed:
        emit({"type": "node", "node": "compressor", "label": "Context compressed to fit token budget"})

    tools = state.custom_tools or []
    # Dynamic persona/domain steering — applies to custom stages too.
    steering = resolve_steering(persona)
    profile = f"## Project profile\n{state.project_profile}\n\n" if state.project_profile else ""
    system = render_prompt("policy.responsible_ai") + "\n\n" + (f"{steering}\n\n" if steering else "") + profile + render_prompt(
        "phase.custom.system", persona=persona, stage_name=state.stage_name or "Custom stage",
        outputs=", ".join(outputs), tools=", ".join(tools) or "(none)",
        tech_stack=state.tech_stack,
    )
    # An optional PM-chosen library prompt layers extra, stage-specific instruction.
    if state.custom_prompt_id:
        try:
            system += "\n\n" + render_prompt(state.custom_prompt_id)
        except KeyError:
            log.warning("custom phase promptId '%s' not in the library — ignored", state.custom_prompt_id)
    user_input = state.user_input or f"Produce the {state.stage_name} deliverable."
    if state.extra_context:
        user_input = f"{user_input}\n\n{state.extra_context}"
    user = render_prompt("phase.custom.user", user_input=user_input, context_block=context_block or "(none)")

    data, result = await deps.llm.generate_json(
        intent="generation", tag=f"custom_stage{state.current_phase}",
        temperature=0.2, max_tokens=6144, schema=CustomPhaseOutput,
        model=state.model_overrides.get("generate") or None,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    deps.audit.record(
        project_id=state.project_id, phase=state.current_phase, agent_role=persona,
        event="ai.generation", provider=result.provider, model=result.model,
        prompt_tokens=result.usage["promptTokens"], completion_tokens=result.usage["completionTokens"],
        artefact_body=result.content, detail={"custom": True, "outputs": outputs,
                                              "toolCalls": [c.tool for c in data.toolCalls]},
    )

    # One artifact PER declared output type: use the model's matching
    # deliverable, falling back to the first produced (or a placeholder) so every
    # declared output is materialised and typed for downstream stages.
    by_output = {d.output: d.content for d in data.deliverables if d.content.strip()}
    fallback = next((d.content for d in data.deliverables if d.content.strip()), "")
    artifacts: list[ContextArtifact] = []
    for out_type in outputs:
        body = by_output.get(out_type) or fallback or f"# {state.stage_name}\n\n(No content generated.)"
        artifacts.append(await _save_artifact(
            deps, state, emit, type_=out_type, title=f"{state.stage_name} — {out_type}" if len(outputs) > 1
            else (state.stage_name or out_type),
            content=body, summary=body[:300], exact=True,
        ))

    # Tool plan: schedule ONLY declared tools, routed through the
    # deferral — queued now, executed on gate approval by the approver (safe: no
    # external side-effects before sign-off). Undeclared/hallucinated tools ignored.
    declared = set(tools)
    scheduled = 0
    for call in data.toolCalls:
        if call.tool in declared:
            await _publish(deps, emit, call.tool, dict(call.args))
            scheduled += 1
        else:
            log.warning("custom phase proposed undeclared tool '%s' — ignored", call.tool)

    tool_note = f" {scheduled} tool action(s) queued for publish on approval." if scheduled else ""
    return PhaseAgentResult(
        summary=f"{state.stage_name}: {persona} produced {len(artifacts)} deliverable(s) covering "
                f"{', '.join(outputs)}.{tool_note} Awaiting gate sign-off.",
        new_artifacts=artifacts, gate_status="PENDING_REVIEW",
    )


_RUNNERS: dict[int, Callable[[AgentDeps, AgentState, Emit], Awaitable[PhaseAgentResult]]] = {
    1: _run_phase1, 2: _run_phase2, 3: _run_phase3, 4: _run_phase4, 5: _run_phase5, 6: _run_phase6,
    7: _run_custom, # data-driven custom phase
}

# MCP tools each template drives, in call order — the run visualizer shows these
# as the expected execution path before/while the agent works.
TEMPLATE_TOOLS: dict[int, list[str]] = {
    1: ["jira_create_epic", "jira_create_story", "confluence_publish_prd"],
    2: ["amazonq_generate_cloudcraft", "github_commit_diagrams", "confluence_publish_hld"],
    3: ["spectral_lint_openapi", "github_commit_lld_artefacts", "confluence_publish_lld"],
    4: ["jira_create_xray_test", "restassured_generate_tests", "playwright_generate_tests",
        "jmeter_generate_plan", "locust_generate_test"],
    5: ["github_commit_pipeline_config", "trivy_scan_image", "aws_secrets_check"],
    6: ["github_create_branch", "github_commit_code", "postman_run_collection",
        "playwright_run_tests", "k6_run_test", "zap_baseline_scan", "sonarqube_analyse"],
}


async def run_phase_agent(deps: AgentDeps, state: AgentState, emit: Emit) -> PhaseAgentResult:
    # The stage's TEMPLATE picks the generation engine; the runtime slot
    # (current_phase = workflow seq) only labels where results are recorded.
    runner = _RUNNERS.get(state.stage_template)
    if not runner:
        raise SdlcError("VALIDATION_FAILED", f"No generation engine for template {state.stage_template}")

    # Governance: when publish-on-approval is enabled, activate a per-run
    # sink so external writes are QUEUED, not executed. The collected actions ride
    # back on the result for the caller to persist against the phase; they replay
    # only after the gate is approved. Disabled → sink stays None → legacy path.
    defer = getattr(deps.settings, "PUBLISH_ON_APPROVAL", True)
    token = _publish_sink.set([] if defer else None)
    try:
        result = await runner(deps, state, emit)
    finally:
        collected = _publish_sink.get()
        _publish_sink.reset(token)
    if defer and collected:
        result.publish_actions = collected
    return result
