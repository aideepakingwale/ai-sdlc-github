"""POST /api/chat lifecycle (Module 2 §1): input guardrail → session load →
LangGraph invocation → output guardrail → persistence → audit → SSE stream."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from typing import Any, AsyncIterator, Callable

from redis.asyncio import Redis

from ..agile.rules import assert_stage_mutable, level_peers
from ..agents.phase_agents import EXTERNAL_WRITE_TOOLS, TEMPLATE_TOOLS, AgentDeps
from ..services.plan_model import build_model_catalog, derive_plan_steps
from ..agents.prompts import build_phase_prompt
from ..config import Settings
from ..domain.errors import SdlcError
from ..domain.models import AgentState, ContextArtifact, UserPublic
from ..graph.pipeline import PIPELINE_NODES, build_pipeline, run_pipeline
from ..services.applicability import (
    GENERATED_ARTIFACTS, TOOL_ARTIFACT, derive_traits, inapplicable_types, project_corpus,
    resolve_traits, trait_values, traits_prompt,
)
from ..services.model_router import classify_tier
from ..services.skills import SKILLS
from ..repos.aws import DynamoStore
from ..repos.pg import Database
from .audit import AuditService
from .authz import AuthzService
from .flow import STALE_STATUSES, transitive_downstream_seqs
from .guardrails import enforce_input, sanitise_output
from .telemetry import set_run_context

log = logging.getLogger("chat")

Emit = Callable[[dict[str, Any]], None]


class ChatService:
    def __init__(
        self, db: Database, redis: Redis, dynamo: DynamoStore, audit: AuditService,
        authz: AuthzService, workflow: Any, agent_deps: AgentDeps, settings: Settings,
        publisher: Any = None,
    ) -> None:
        self._db = db
        self._redis = redis
        self._dynamo = dynamo
        self._audit = audit
        self._authz = authz
        self._workflow = workflow
        self._deps = agent_deps
        self._settings = settings
        self._publisher = publisher  # PublishService (D-67): stores deferred publish plans
        self._pipeline = build_pipeline()
        # Set by the composition root: runs sprint/release bookkeeping + the auto-gate once a stage reaches
        # PENDING_REVIEW. None = classic behaviour (waterfall-only deployments, tests).
        self.lifecycle: Any = None

    async def _run_stage_pipeline(
        self, state: AgentState, *, project_id: str, seq: int, reviewer_role: str,
        prev_status: str, emit: Emit, summary: str,
    ):
        """Run one stage through the pipeline with failure recovery (D-67): if
        generation or a tool raises, restore the phase from IN_PROGRESS to its
        prior status (so it is re-triggerable, not stuck) and re-raise so the SSE
        stream surfaces the error instead of the UI hanging."""
        try:
            return await run_pipeline(
                self._pipeline, state, deps=self._deps, emit=emit, phase_states_summary=summary,
            )
        except Exception as err:
            try:
                await self._dynamo.put_phase_state(
                    project_id=project_id, phase=seq,
                    status=prev_status if prev_status != "IN_PROGRESS" else "NOT_STARTED",
                    reviewer_role=reviewer_role,
                )
            except Exception:  # noqa: BLE001 — best-effort state restore
                log.exception("failed to restore phase state after stage error")
            self._audit.record(
                project_id=project_id, phase=seq, agent_role="Orchestrator",
                event="stage.failed", detail={"error": str(err)[:300]},
            )
            emit({"type": "error", "code": "STAGE_FAILED",
                  "message": f"Stage {seq} failed: {err}. It has been reset — fix the issue and re-trigger."})
            raise

    async def reset_stage_if_in_progress(self, project_id: str, phase: int) -> None:
        """Safety net (D-111): if a stage is still IN_PROGRESS after its generation job
        ended — e.g. the worker crashed, or an error bypassed the in-flow reset — return
        it to NOT_STARTED so it is never stuck on 'Generating' and can be re-triggered.
        Called by the worker runner on any uncaught failure. Never raises."""
        try:
            st = await self._dynamo.get_phase_state(project_id, phase)
            if (st or {}).get("status") != "IN_PROGRESS":
                return
            try:
                _, stage = await self._stage_for(project_id, phase)
                reviewer = stage.get("reviewerRole", "") or ""
            except Exception:  # noqa: BLE001
                reviewer = (st or {}).get("reviewer_role") or ""
            await self._dynamo.put_phase_state(project_id=project_id, phase=phase, status="NOT_STARTED", reviewer_role=reviewer)
            self._audit.record(project_id=project_id, phase=phase, agent_role="Orchestrator",
                               event="stage.reset_after_failure", detail={})
            log.info("reset stuck stage %s/%s IN_PROGRESS -> NOT_STARTED after failure", project_id, phase)
        except Exception:  # noqa: BLE001 — best-effort
            log.exception("reset_stage_if_in_progress failed")

    async def _persist_publish_plan(self, project_id: str, seq: int, phase_result: Any) -> None:
        """Store the phase's deferred external-write plan so it can be replayed on
        gate approval (D-67). Replaces any prior plan for the phase."""
        if self._publisher is None:
            return
        try:
            await self._publisher.enqueue(project_id, seq, getattr(phase_result, "publish_actions", []) or [])
        except Exception:  # noqa: BLE001 — never fail generation on queue persistence
            log.exception("failed to persist publish plan for phase %s", seq)

    async def handle(
        self, *, user: UserPublic, project_id: str | None, message: str, emit: Emit,
        referenced_artifact_ids: list[str] | None = None, attachment_ids: list[str] | None = None,
        formwork_ids: list[str] | None = None,
    ) -> None:
        emit({"type": "node", "node": "guardrail", "label": "Input guardrail"})
        try:
            enforce_input(message, channel="chat")
        except SdlcError as err:
            if project_id:  # audit the block when a project context exists
                self._audit.record(
                    project_id=project_id, agent_role="InputGuardrail",
                    event="guardrail.input_blocked", human_reviewer=user.email,
                    detail={"rules": (err.details or {}).get("rules", []), "channel": "chat"},
                )
            raise

        project, session = await self._load_or_create(project_id, message, user)
        phase_id = int(session["current_phase"])
        emit({"type": "session", "projectId": project["id"], "sessionId": session["id"], "phase": phase_id})

        # Dynamic workflow (D-30): find the level (parallel group) holding the
        # current stage slot; a single run generates EVERY ready stage in it.
        wf = await self._workflow.view(project["id"])
        stages_by_seq = {s["seq"]: s for s in wf["stages"]}
        level_seqs = level_peers(wf, phase_id)          # same level AND same release lane
        states = {s["SK"]: s for s in await self._dynamo.list_phase_states(project["id"])}

        pending = [
            stages_by_seq[seq] for seq in level_seqs
            if (states.get(f"PHASE#{seq}") or {}).get("status") == "PENDING_REVIEW"
        ]
        ready = [
            stages_by_seq[seq] for seq in level_seqs
            if (states.get(f"PHASE#{seq}") or {}).get("status") != "APPROVED"
            and stages_by_seq[seq] not in pending
        ]

        if pending and not ready:
            waits = ", ".join(f"**{s['reviewerRole']}** ({s['name']})" for s in pending)
            text = (f"## Gate pending\nThis level is awaiting review: {waits}. Approve or request "
                    "amendments in the Gate Dashboard; the pipeline resumes after sign-off.")
            await self._db.insert_chat_turn(session["id"], phase_id, message, text)
            for s in pending:
                emit({"type": "gate", "phase": s["seq"], "status": "PENDING_REVIEW",
                      "reviewerRole": s["reviewerRole"]})
            emit({"type": "done", "finalResponse": text, "phase": phase_id, "gateStatus": "PENDING_REVIEW"})
            return

        context = [ContextArtifact.model_validate(a) for a in (session["context_window"] or [])]
        has_codebase = (await self._db.count_codebase_files(project["id"])) > 0
        # Rich compose (D-54): resolve the user's curated @references + attachments
        # into one labelled block, injected into every stage run of this turn.
        extra_context = await self._resolve_extra_context(
            project["id"], referenced_artifact_ids or [], attachment_ids or [], formwork_ids or [], emit
        )
        responses: list[str] = []
        last_gate = "IN_PROGRESS"
        # The reviewer feedback that drove this run (if it's an amend
        # regeneration), so the chat history records what was actually requested
        # instead of the generic internal trigger message (transparency, D-47).
        amend_feedback: str | None = None

        if len(ready) > 1:
            emit({"type": "node", "node": "executor",
                  "label": f"Parallel group: running {len(ready)} stages of this level"})

        for stage in ready:
            seq = stage["seq"]
            set_run_context(project["id"], seq)  # attribute LLM/tool spans (D-35)
            st = states.get(f"PHASE#{seq}")
            amend = st.get("comments") if st and st.get("status") == "AMEND_REQUESTED" else None
            if amend:
                amend_feedback = amend

            sp_row = await self._db.get_stage_plan(project["id"], seq)
            stage_context, stage_extra = list(context), extra_context
            if self.lifecycle is not None and (stage.get("iterationId") or stage.get("releaseId")):
                stage_context, agile_extra = await self.lifecycle.context_for(project["id"], stage, context)
                stage_extra = (extra_context + "\n\n" + agile_extra).strip()
            state = AgentState(
                project_id=project["id"], session_id=session["id"], current_phase=seq,
                stage_template=stage["template"], stage_name=stage["name"],
                stage_reviewer=stage["reviewerRole"],
                user_input=message, context_window=list(stage_context), amend_comments=amend,
                tech_stack=project.get("tech_stack") or "Node.js + TypeScript",
                project_profile=self._project_profile(project),
                has_codebase=has_codebase, extra_context=stage_extra,
                model_overrides=self._model_overrides_from(self._step_overrides(sp_row)),  # per-step model (D-68)
                per_artifact=await self._per_artifact_enabled(),  # runtime split toggle (D-106)
                **self._custom_fields(stage),  # custom phase config (D-74)
            )
            prev_status = (st or {}).get("status", "NOT_STARTED") if st else "NOT_STARTED"
            await self._dynamo.put_phase_state(
                project_id=project["id"], phase=seq, status="IN_PROGRESS",
                reviewer_role=stage["reviewerRole"], comments=amend,
            )
            summary = await self._status_summary(project["id"], project["name"], wf, phase_id)
            final_state, phase_result = await self._run_stage_pipeline(
                state, project_id=project["id"], seq=seq, reviewer_role=stage["reviewerRole"],
                prev_status=prev_status, emit=emit, summary=summary,
            )
            if phase_result:
                context = final_state.context_window
                # Persist the deferred external-write plan for this stage (D-67).
                await self._persist_publish_plan(project["id"], seq, phase_result)
                if final_state.gate_status == "PENDING_REVIEW":
                    await self._dynamo.put_phase_state(
                        project_id=project["id"], phase=seq,
                        status="PENDING_REVIEW", reviewer_role=stage["reviewerRole"],
                    )
                    emit({"type": "gate", "phase": seq, "status": "PENDING_REVIEW",
                          "reviewerRole": stage["reviewerRole"]})
                    self._audit.record(
                        project_id=project["id"], phase=seq, agent_role=stage["persona"],
                        event="gate.pending_review",
                        detail={"reviewerRole": stage["reviewerRole"], "stage": stage["key"],
                                "amendCycle": bool(amend)},
                    )
                    await self._after_review_ready(project["id"], seq, final_state, user.email)
            last_gate = final_state.gate_status
            responses.append(final_state.final_response)
            if not phase_result:  # status/fast-path answer — one response is enough
                break

        await self._db.update_context_window(session["id"], context)
        emit({"type": "node", "node": "guardrail", "label": "Output guardrail"})
        combined = "\n\n---\n\n".join(responses) if responses else "No runnable stage at this level."
        safe_response, masked = sanitise_output(combined)
        if masked:
            self._audit.record(
                project_id=project["id"], phase=phase_id, agent_role="OutputGuardrail",
                event="guardrail.output_masked", detail={"rules": masked},
            )

        # Record the reviewer's actual feedback in the transcript on an amend
        # regeneration, not the internal trigger, so the history shows what was
        # requested (D-47).
        turn_message = (
            f"🔁 Gate review — changes requested:\n\n{amend_feedback}" if amend_feedback else message
        )
        await self._db.insert_chat_turn(session["id"], phase_id, turn_message, safe_response)
        await self._redis.set(
            f"session:{session['id']}",
            json.dumps({"projectId": project["id"], "currentPhase": phase_id}),
            ex=self._settings.SESSION_TTL_HOURS * 3600,
        )
        emit({"type": "done", "finalResponse": safe_response, "phase": phase_id, "gateStatus": last_gate})

    async def plan_preview(self, *, project_id: str, user: UserPublic, message: str = "") -> dict[str, Any]:
        """Viz (D-31): what WOULD run on the next chat turn — the ready stages of
        the current level with their plan steps, model tier, expected MCP tools
        and stage-scoped skills — without executing anything."""
        await self._authz.assert_project_access(project_id, user)
        project = await self._db.get_project(project_id)
        if not project:
            raise SdlcError("NOT_FOUND", f"Project {project_id} not found")
        phase_id = int(project["current_phase"])

        wf = await self._workflow.view(project_id)
        stages_by_seq = {s["seq"]: s for s in wf["stages"]}
        level_seqs = level_peers(wf, phase_id)          # same level AND same release lane
        states = {s["SK"]: s for s in await self._dynamo.list_phase_states(project_id)}

        pending = [
            stages_by_seq[seq] for seq in level_seqs
            if (states.get(f"PHASE#{seq}") or {}).get("status") == "PENDING_REVIEW"
        ]
        ready = [
            stages_by_seq[seq] for seq in level_seqs
            if (states.get(f"PHASE#{seq}") or {}).get("status") != "APPROVED"
            and stages_by_seq[seq] not in pending
        ]

        text = message.strip()
        stages_out = []
        for stage in ready:
            probe = text or f"Generate {stage['name']} ({', '.join(stage['outputs'])})"
            tier = classify_tier(probe, has_tools=True, context_tokens=0)
            tools = TEMPLATE_TOOLS.get(stage["template"], [])
            skills = [
                {"id": s.id, "name": s.name, "tier": s.tier}
                for s in SKILLS if s.phase in (None, stage["template"])
            ]
            steps = [
                {"id": "generate", "tool": "llm",
                 "description": f"{stage['persona']} generates {', '.join(stage['outputs'])}"},
                *[{"id": t, "tool": t, "description": f"MCP tool: {t}"} for t in tools],
                {"id": "gate", "tool": "gate",
                 "description": f"Open HITL gate for {stage['reviewerRole']} review"},
            ]
            stages_out.append({
                "seq": stage["seq"], "key": stage["key"], "name": stage["name"],
                "template": stage["template"], "persona": stage["persona"],
                "reviewerRole": stage["reviewerRole"], "tier": tier,
                "steps": steps, "expectedTools": tools, "skills": skills,
            })

        return {
            "projectId": project_id, "currentPhase": phase_id,
            "workflowVersion": wf["version"], "nodes": PIPELINE_NODES,
            "parallel": len(stages_out) > 1,
            "stages": stages_out,
            "pendingGates": [
                {"seq": s["seq"], "name": s["name"], "reviewerRole": s["reviewerRole"]} for s in pending
            ],
        }

    # ------------------------------------------------------------ Plan Review & Edit gate (D-56)
    async def _stage_for(self, project_id: str, phase: int) -> tuple[dict, dict]:
        wf = await self._workflow.view(project_id)
        stage = next((s for s in wf["stages"] if s["seq"] == phase), None)
        if not stage:
            raise SdlcError("NOT_FOUND", f"No stage at position {phase} in this workflow")
        return wf, stage

    def _stage_writers(self, stage: dict) -> list[str]:
        return stage.get("writeRoles") or stage.get("team") or [stage["reviewerRole"]]

    async def can_write_stage(self, project_id: str, phase: int, user: UserPublic) -> bool:
        """Public wrapper that resolves the stage by position (D-57)."""
        _, stage = await self._stage_for(project_id, phase)
        return await self._can_write_stage(project_id, stage, user)

    async def _can_write_stage(self, project_id: str, stage: dict, user: UserPublic) -> bool:
        if user.role == "SUPER_ADMIN":
            return True
        project = await self._db.get_project(project_id)
        if user.role == "PROJECT_MANAGER" and project and project["created_by"] == user.id:
            return True
        # D-90: per-user ACL is authoritative when the stage defines it (a user's
        # role varies per project, so role is not a reliable key). Falls back to
        # the role-based writers for legacy/role-defined stages.
        perms = stage.get("userPerms") or []
        if perms:
            email = (getattr(user, "email", "") or "").strip().lower()
            writers = {(p.get("email") or "").strip().lower()
                       for p in perms if p.get("write")}
            return email in writers
        membership = await self._authz.get_membership_role(project_id, user.id)
        return membership in self._stage_writers(stage)

    async def _assemble_prompt_preview(self, project: dict, session: dict, stage: dict, overlay: dict, emit: Emit):
        """Assemble the exact system+user prompt the stage would run with, given the
        editable overlay — WITHOUT calling the LLM. The proprietary craft/quality-bar
        core is included read-only; only the overlay (instructions + curated context)
        is user-editable (D-56)."""
        context = [ContextArtifact.model_validate(a) for a in (session.get("context_window") or [])]
        context_block = "\n\n".join(
            f"### [Phase {a.phase}] {a.type}: {a.title}\n{(a.content or a.summary)[:1200]}" for a in context
        )
        snippets = await self._deps.rag.retrieve(overlay.get("promptOverlay") or stage["name"], project["id"])
        rag_block = self._deps.rag.render_block(snippets)
        canon_block = await self._deps.canon.render_block(project["id"], stage["template"]) if self._deps.canon else ""
        produces = list(stage.get("outputs") or [])
        formwork_block = await self._deps.formworks.render_block(project["id"], produces) if self._deps.formworks else ""
        extra_context = await self._resolve_extra_context(
            project["id"], overlay.get("referencedArtifactIds") or [], overlay.get("attachmentIds") or [],
            overlay.get("formworkIds") or [], emit,
        )
        user_input = overlay.get("promptOverlay") or f"Generate {', '.join(produces)} for '{stage['name']}'."
        if stage["template"] == 7:
            # Custom phase (D-74): preview the generic prompt the runner will use —
            # build_phase_prompt is for the six built-in engines only.
            from .prompt_library import render as render_prompt
            from .steering import resolve_steering
            persona = stage.get("persona") or "Specialist"
            steering = resolve_steering(persona)
            system = render_prompt("policy.responsible_ai") + "\n\n" + (f"{steering}\n\n" if steering else "") + render_prompt(
                "phase.custom.system", persona=persona, stage_name=stage["name"],
                outputs=", ".join(produces or ["DELIVERABLE"]),
                tools=", ".join(stage.get("tools") or []) or "(none)",
                tech_stack=project.get("tech_stack") or "Node.js + TypeScript",
            )
            if stage.get("promptId"):
                try:
                    system += "\n\n" + render_prompt(stage["promptId"])
                except KeyError:
                    pass
            body = f"{user_input}\n\n{extra_context}" if extra_context else user_input
            user = render_prompt("phase.custom.user", user_input=body, context_block=context_block or "(none)")
        else:
            system, user = build_phase_prompt(
                phase=stage["template"], context_block=context_block, rag_block=rag_block,
                user_input=user_input, amend_comments=None,
                tech_stack=project.get("tech_stack") or "Node.js + TypeScript",
                project_profile=self._project_profile(project),
                has_codebase=(await self._db.count_codebase_files(project["id"])) > 0,
                canon_block=canon_block, formwork_block=formwork_block, user_context_block=extra_context,
                quality_gate_enabled=getattr(self._settings, "QUALITY_GATE_ENABLED", True),
                coverage_min=getattr(self._settings, "COVERAGE_MIN_PERCENT", 80),
                lint_required=getattr(self._settings, "LINT_REQUIRED", True),
            )
        return system, user, extra_context, context, snippets

    @staticmethod
    def _step_overrides(row: Any) -> dict[str, Any]:
        """Read persisted per-step model overrides (D-68), tolerating a JSONB value
        that decodes as either a dict or a JSON string."""
        if not row:
            return {}
        raw = row["step_overrides"] if "step_overrides" in row else {}
        # Tolerate a value that was accidentally double/triple-encoded (a jsonb that
        # holds a JSON *string*): decode until it is no longer a str. Always return a
        # dict so callers can safely .items() it (a str here previously crashed a run).
        for _ in range(4):
            if not isinstance(raw, str):
                break
            try:
                raw = json.loads(raw or "{}")
            except json.JSONDecodeError:
                raw = {}
                break
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _project_profile(project: dict) -> str:
        """Compact project profile threaded into every stage (#4): name, tech
        stack and integration targets, so the whole run stays configuration-aware."""
        parts = [f"- Project: {project.get('name') or '(unnamed)'}"]
        if project.get("tech_stack"):
            parts.append(f"- Technology stack: {project['tech_stack']}")
        for label, key in (
            ("GitHub repository", "github_repo"),
            ("Atlassian site", "atlassian_site_url"),
            ("Jira project key", "jira_project_key"),
            ("Confluence space key", "confluence_space_key"),
        ):
            if project.get(key):
                parts.append(f"- {label}: {project[key]}")
        return "\n".join(parts)

    async def _clarification_questions(
        self, *, project: dict, stage: dict, user_input: str,
        context: list[ContextArtifact], extra_context: str,
    ) -> list[dict[str, Any]]:
        """Ambiguity pre-check (#1/D-108): return STRUCTURED clarifying questions (each
        with predefined options for the UI cards) when the inputs are too ambiguous to
        generate without assuming; [] to proceed. Never raises — a failed check must
        not block generation."""
        from ..agents.schemas import ClarificationOutput
        from ..domain.models import get_phase
        from .prompt_library import render as render_prompt
        from .steering import resolve_mandatory_inputs
        persona = stage.get("persona") or ""
        if not persona:
            try:
                persona = get_phase(stage["template"]).agent_persona
            except Exception:
                persona = "Specialist"
        mandatory = resolve_mandatory_inputs(persona)
        mandatory_block = "\n".join(f"- {m}" for m in mandatory) or "- (no persona-specific mandatory inputs)"
        # Requirement analysis warrants a deeper holistic sweep — allow more questions.
        is_requirements = stage.get("template") == 1 or persona.strip().upper() in ("BA", "PO") or any(
            k in persona.lower() for k in ("business analyst", "product owner", "requirements", "product manager")
        )
        max_questions = (
            self._settings.CLARIFY_MAX_QUESTIONS_REQUIREMENTS if is_requirements
            else self._settings.CLARIFY_MAX_QUESTIONS
        )
        # Deterministic floor (model-independent): if there is genuinely nothing to
        # work from — no request, no attached context and no upstream artifacts —
        # the model must NOT invent scope. Return the persona's mandatory-input
        # questions directly rather than trusting a weak model to notice the gap.
        insufficient = (
            not (user_input or "").strip()
            and not (extra_context or "").strip()
            and not context
        )
        if insufficient:
            base = mandatory or [
                "What business problem are we solving, and for whom?",
                "What is explicitly in scope and out of scope?",
                "What are the measurable success criteria?",
                "Are there compliance, legal or data-privacy obligations (e.g. GDPR, PCI-DSS)?",
            ]
            # Even open discovery questions get selectable options now (D-112): the
            # topic-aware synthesiser offers choices so the user can click, not only type.
            return [
                self._ensure_options(
                    {"id": f"need-{i}", "question": f"Please provide: {b}", "header": "",
                     "options": [], "multiSelect": False, "rationale": ""})
                for i, b in enumerate(base)
            ][:max_questions]
        digest = "\n".join(f"- [P{a.phase}] {a.type}: {a.title}" for a in context[-20:]) or "(no upstream artifacts yet)"
        if extra_context:
            digest = f"{digest}\n\nCurated context:\n{extra_context[:2000]}"
        req = user_input.strip() or f"Produce {', '.join(stage.get('outputs') or ['the deliverables'])} for the '{stage['name']}' stage."
        try:
            out, _ = await self._deps.llm.generate_json(
                intent="standard", tier="auto", tag="clarify", max_tokens=1500, max_attempts=2,
                schema=ClarificationOutput,
                messages=[
                    {"role": "system", "content": render_prompt("policy.clarification") + "\n\n" + render_prompt(
                        "clarify.system", persona=persona, stage_name=stage["name"],
                        max_questions=max_questions,
                        mandatory_inputs=mandatory_block)},
                    {"role": "user", "content": render_prompt(
                        "clarify.user", request=req, project_profile=self._project_profile(project),
                        context_digest=digest)},
                ],
            )
        except Exception as err:  # noqa: BLE001 — a failed check must not block generation
            log.info("clarify check errored (%s); proceeding without questions", err)
            return []
        # Drop any blank questions (lenient schema), guarantee each has selectable
        # options (D-112), then apply the cap.
        qs = [self._ensure_options(q.model_dump()) for q in out.questions if (q.question or "").strip()]
        log.info("clarify: needs=%s questions=%d stage=%s", out.needs_clarification, len(qs), stage.get("name"))
        return qs[:max_questions] if (out.needs_clarification and qs) else []

    # Deterministic option sets by topic, so a clarifying question ALWAYS offers
    # selectable choices even when the model returns it without any (D-112). Keyed by
    # a keyword that may appear in the question or its header.
    _OPTION_LIBRARY: list[tuple[tuple[str, ...], list[tuple[str, str]]]] = [
        (("cloud", "platform", "infra", "deployment", "hosting", "provider"),
         [("AWS", "Amazon Web Services"), ("Azure", "Microsoft Azure"), ("GCP", "Google Cloud"),
          ("On-prem / Kubernetes", "Self-hosted or private cluster")]),
        (("complian", "regulat", "gdpr", "pci", "hipaa", "privacy", "legal", "data residency", "retention"),
         [("GDPR", "EU personal-data protection"), ("PCI-DSS", "Payment card data"),
          ("HIPAA", "Health information"), ("None", "No specific regime applies")]),
        (("auth", "identity", "sso", "login", "access control"),
         [("OAuth2 / OIDC", "Token-based SSO"), ("SAML", "Enterprise SSO"),
          ("API keys", "Service-to-service"), ("Username / password", "Basic credentials")]),
        (("database", "datastore", "persistence", "storage engine"),
         [("PostgreSQL", "Relational"), ("MySQL", "Relational"), ("MongoDB", "Document"),
          ("Other", "Specify in Other")]),
        (("protocol", "integration pattern", "messaging", "transport", "queue", "interface"),
         [("Message queue (MQ)", "Async messaging"), ("REST / HTTP", "Synchronous API"),
          ("Kafka / streaming", "Event stream"), ("File transfer", "Batch files")]),
        (("frequency", "schedule", "cadence", "how often", "real-time", "latency"),
         [("Near real-time", "As events occur"), ("Hourly", "Every hour"),
          ("Daily", "Once a day"), ("Batch", "Scheduled batch")]),
        (("environment", "stage", "target env"),
         [("Production", "Live"), ("Staging / UAT", "Pre-prod"), ("Development", "Dev only")]),
    ]

    @classmethod
    def _ensure_options(cls, q: dict[str, Any]) -> dict[str, Any]:
        """Guarantee a clarifying question offers selectable options (D-112): keep the
        model's valid options; otherwise synthesise topic-appropriate ones, falling
        back to a generic pair so the user can always click instead of typing."""
        opts = [o for o in (q.get("options") or [])
                if isinstance(o, dict) and str(o.get("label") or "").strip()]
        if len(opts) < 2:
            text = f"{q.get('header', '')} {q.get('question', '')}".lower()
            chosen: list[tuple[str, str]] | None = None
            for keys, lib in cls._OPTION_LIBRARY:
                if any(k in text for k in keys):
                    chosen = lib
                    break
            if chosen is None:
                # Yes/No for a decision question; else a safe generic pair.
                if re.match(r"\s*(should|is|are|do|does|can|will|would|has|have|shall)\b", text):
                    chosen = [("Yes", "Proceed with this"), ("No", "Do not")]
                else:
                    chosen = [("Use your recommended default", "Let the agent choose the best option"),
                              ("I'll specify", "Type the specifics in Other")]
            opts = [{"label": lbl, "description": desc} for lbl, desc in chosen]
        q["options"] = opts
        return q

    async def answer_clarification(
        self, *, project_id: str, phase: int, user: UserPublic, answers: list[dict[str, Any]],
    ) -> None:
        """Fold the reviewer's answers to the clarifying questions into the stage's
        prompt overlay and clear the pending questions (D-108), so the next run has
        the context and skips the check. Write-permission required."""
        _, stage = await self._stage_for(project_id, phase)
        if not await self._can_write_stage(project_id, stage, user):
            raise SdlcError("FORBIDDEN", f"Answering the '{stage['name']}' clarification requires write permission ({' or '.join(self._stage_writers(stage))})")
        row = await self._db.get_stage_plan(project_id, phase)
        existing = ((row["prompt_overlay"] if row else "") or "").strip()
        lines: list[str] = []
        for a in answers:
            q = str(a.get("question") or "").strip()
            ans = str(a.get("answer") or "").strip() or "(no preference — use your best judgement)"
            if q:
                lines.append(f"- {q}\n  → {ans}")
        block = "## Clarifications (confirmed by the reviewer)\n" + "\n".join(lines)
        new_overlay = f"{existing}\n\n{block}" if existing else block
        await self._db.upsert_stage_plan(
            project_id=project_id, phase=phase, prompt_overlay=new_overlay,
            referenced_artifact_ids=(row["referenced_artifact_ids"] if row else []) or [],
            attachment_ids=(row["attachment_ids"] if row else []) or [],
            formwork_ids=(row["formwork_ids"] if row else []) or [],
            step_overrides=self._step_overrides(row),  # normalized dict — never re-encode the raw jsonb string (prevents double-encoding)
            origin="clarification", updated_by=user.email,
        )
        await self._db.set_stage_clarification(project_id, phase, None)  # clear pending questions
        # Persist the clarification exchange into the discussion history (D-112) so it
        # is visible, timestamped, and survives navigation — not just folded into the
        # overlay. Best-effort: never block answering on a history write.
        try:
            session = await self._db.get_session(project_id)
            if session and lines:
                await self._db.insert_chat_turn(
                    session["id"], phase,
                    "Answered the clarifying questions:\n" + "\n".join(lines),
                    "Thanks — your answers are recorded and will guide generation.",
                )
        except Exception:  # noqa: BLE001
            log.warning("could not persist clarification turn", exc_info=True)
        self._audit.record(project_id=project_id, phase=phase, agent_role="Orchestrator",
                           event="clarification.answered", human_reviewer=user.email,
                           detail={"stage": stage["key"], "count": len(lines)})

    async def save_discussion_turn(
        self, *, project_id: str, phase: int, user: UserPublic,
        user_message: str, agent_message: str = "",
    ) -> None:
        """Persist one turn of the pre-generation planning discussion (Discuss & refine)
        into the saved history (D-112), so the back-and-forth before triggering survives
        navigation and is timestamped. Write-permission required; best-effort body."""
        _, stage = await self._stage_for(project_id, phase)
        if not await self._can_write_stage(project_id, stage, user):
            raise SdlcError("FORBIDDEN", f"Posting to the '{stage['name']}' discussion requires write permission ({' or '.join(self._stage_writers(stage))})")
        um = (user_message or "").strip()
        if um:
            enforce_input(um, channel="plan")
        if not um and not (agent_message or "").strip():
            return
        session = await self._db.get_session(project_id)
        if not session:
            return
        await self._db.insert_chat_turn(session["id"], phase, um, (agent_message or "").strip())

    @staticmethod
    def _custom_fields(stage: dict) -> dict[str, Any]:
        """Custom-phase config → AgentState fields (D-74). Only for template 7; the
        built-in engines ignore these."""
        if stage.get("template") != 7:
            return {}
        return {
            "agile_role": stage.get("agileRole"),
            "iteration_id": stage.get("iterationId"),
            "custom_persona": stage.get("persona", "") or "",
            "custom_prompt_id": stage.get("promptId", "") or "",
            "custom_outputs": list(stage.get("outputs") or []),
            "custom_tools": list(stage.get("tools") or []),
        }

    @staticmethod
    def _model_overrides_from(step_overrides: dict[str, Any]) -> dict[str, str]:
        """Flatten persisted step overrides to { stepId: 'provider/model' } for the
        run, keeping only entries that actually pin a model (D-68)."""
        out: dict[str, str] = {}
        for step_id, entry in (step_overrides or {}).items():
            model = entry.get("model") if isinstance(entry, dict) else None
            if model:
                out[step_id] = model
        return out

    async def _per_artifact_enabled(self) -> bool:
        """Effective per-artifact split flag for a run (D-106): the runtime setting
        `per_artifact_generation` (Super-Admin toggle, mirrored to Redis) OR the env
        default. Read once per run when the state is built; never raises."""
        if getattr(self._settings, "PER_ARTIFACT_GENERATION", False):
            return True
        try:
            raw = await self._redis.get("sdlc:settings:per_artifact_generation")
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode()
            return str(raw).strip().lower() == "true"
        except Exception:
            return False

    async def _plan_max_tokens(self) -> int:
        """Effective output budget for the LLM stage planner (D-112): the runtime
        setting `plan_max_tokens` (Super-Admin, mirrored to Redis) OR the env default.
        Read per plan display; never raises."""
        env_default = getattr(self._settings, "PLAN_MAX_TOKENS", 8000)
        try:
            raw = await self._redis.get("sdlc:settings:plan_max_tokens")
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode()
            if raw is not None and str(raw).strip():
                v = int(str(raw).strip())
                if v > 0:
                    return v
        except Exception:
            pass
        return env_default

    def _configured_tools(self, tools: list[str]) -> list[str]:
        """Filter a stage's candidate tools to those whose integration is actually
        configured (D-105), so the plan never proposes a tool that cannot run. Jira/
        Confluence need Atlassian MCP; github_* needs GitHub MCP; the rest are the
        in-house tool-connector tools (always available)."""
        s = self._settings

        def available(t: str) -> bool:
            if t.startswith("jira_") or t.startswith("confluence_"):
                return bool(getattr(s, "ATLASSIAN_MCP_ENABLED", False))
            if t.startswith("github_"):
                return bool(getattr(s, "GITHUB_MCP_ENABLED", False))
            return True

        return [t for t in tools if available(t)]

    @staticmethod
    def _norm_type(text: str) -> str:
        return re.sub(r"[^A-Z0-9]+", "_", (text or "").upper()).strip("_")

    async def resolve_project_traits(
        self, *, project: dict, phase: int, user_text: str, upstream: list[str], allow_llm: bool,
    ) -> dict[str, dict[str, Any]]:
        """LLM decides, code enforces. The model JUDGES what the project is (ui/api/database/
        cloud/aws/container/service, each with evidence + confidence); this code turns that into
        decisions. Precedence per trait: human override > AI judgement > keyword rules (only when
        the AI is unavailable). The AI call happens only on an explicit plan display (allow_llm);
        trigger/persist paths reuse the stored judgement, so the plan and the generation agree."""
        from ..agents.schemas import ProjectTraitsIntel

        corpus = project_corpus(project=project, user_text=user_text, upstream=upstream)
        sig = hashlib.sha256(corpus.encode()).hexdigest()[:16]
        row = await self._db.get_stage_traits(project["id"], phase)
        llm: dict[str, Any] | None = json.loads(row["traits_json"]) if row else None
        if (allow_llm and getattr(self._settings, "INTELLIGENT_PLANNING", True)
                and (row is None or row["sig"] != sig)):
            try:
                system, usr = traits_prompt(project=project, user_text=user_text, upstream=upstream)
                data, _ = await self._deps.llm.generate_json(
                    intent="standard", tag="project_traits", temperature=0, max_tokens=900,
                    schema=ProjectTraitsIntel, max_attempts=1,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": usr}],
                )
                llm = data.model_dump()
                await self._db.upsert_stage_traits(project["id"], phase, sig, json.dumps(llm))
            except Exception as err:  # noqa: BLE001 — advisory; keep any stored judgement, else rules
                log.info("trait classification unavailable (%s); using stored/keyword traits", err)
        overrides = await self._db.get_trait_overrides(project["id"])
        detail = resolve_traits(llm, overrides, derive_traits(corpus=corpus))
        if llm and llm.get("projectType"):
            detail["_projectType"] = {"value": None, "source": "ai", "evidence": llm["projectType"], "confidence": 1.0}
        return detail

    async def set_trait_override(self, *, project_id: str, trait: str, value: str | None,
                                 user: UserPublic) -> dict[str, Any]:
        """A project lead pins a trait to present/absent (or clears it with None). Wins over the AI."""
        from .applicability import TRAIT_NAMES
        from .canon import CanonService

        await self._authz.assert_project_access(project_id, user)
        if trait not in TRAIT_NAMES:
            raise SdlcError("VALIDATION_FAILED", f"Unknown trait '{trait}'")
        if value not in (None, "present", "absent"):
            raise SdlcError("VALIDATION_FAILED", "value must be present, absent or null")
        await CanonService(self._db, self._authz, self._audit).assert_can_author(project_id, user)
        if await self._redis.keys(f"run:{project_id}:*"):
            raise SdlcError("GATE_CONFLICT", "A stage is generating — project settings are locked until it finishes")
        await self._db.set_trait_override(project_id, trait, value, user.id)
        self._audit.record(project_id=project_id, phase=0, agent_role="Orchestrator",
                           event="project.trait_overridden", human_reviewer=user.email,
                           detail={"trait": trait, "value": value})
        return {"projectId": project_id, "trait": trait, "value": value}

    def _apply_applicability(self, intel: dict[str, Any], excluded: dict[str, str]) -> dict[str, Any]:
        """Items the project cannot use are NOT part of the plan: they are removed from the
        proposed outputs (whatever the LLM said), and are never generated. The reason is kept
        only as a short note so the reviewer can see what was left out and why."""
        if not excluded:
            return intel
        gone = set(excluded)
        will = [a for a in intel.get("willProduce", []) if self._norm_type(a.get("output", "")) not in gone]
        extras = [x for x in intel.get("suggestedArtifacts", [])
                  if self._norm_type(x.get("name", "")) not in gone]
        checks = [f"{t} — {why}" for t, why in excluded.items()]
        return {**intel, "willProduce": will, "suggestedArtifacts": extras, "promptChecks": checks}

    def _deterministic_proposal(
        self, stage: dict, overlay: dict,
        attachments: list[dict] | None = None, formworks: list[dict] | None = None,
    ) -> dict[str, Any]:
        """A model-free advise→decide proposal built from the stage's declared outputs
        and the attached context (D-112). Guarantees the reviewer always sees what will
        be produced — with per-output checkboxes and the format to follow — even when
        the LLM planner is unavailable (mock, disabled, or a failed/truncated call).
        The LLM proposal, when present, takes precedence over this."""
        outputs = list(stage.get("outputs") or [])
        att = [a.get("filename", "") for a in (attachments or []) if a.get("filename")]
        fw = [f.get("name", "") for f in (formworks or []) if f.get("name")]
        instr = (overlay.get("promptOverlay") or "").strip()
        understood = (
            f"Produce {stage['name']} output" + (f" — {instr[:220]}" if instr else " for this request.")
        )
        will = [{"output": o, "recommended": True, "include": True,
                 "reason": "declared output of this stage"} for o in outputs]
        if att:
            fmt = f"the attached document(s): {', '.join(att)}"
        elif fw:
            fmt = f"the selected template(s): {', '.join(fw)}"
        else:
            fmt = "the stage's default template"
        return {
            "understood": understood, "willProduce": will, "formatSource": fmt,
            "outOfScope": [], "recommendation":
                "Review the outputs below and untick anything you don't want, then trigger the stage.",
            "summary": "", "steps": [], "toolRecommendations": [],
            "skillRecommendations": [], "assumptions": [], "risks": [],
            "cached": False, "deterministic": True,
        }

    async def _intelligent_plan(
        self, *, project: dict, phase: int, stage: dict, overlay: dict,
        available_tools: list[str], skills: list[dict], prior_arts: list[dict], canon_applied: bool,
        attachments: list[dict] | None = None, formworks: list[dict] | None = None,
        allow_compute: bool = True, applicability: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        """LLM-built, context-aware plan for a stage (D-105). Fed the input, tech
        stack, project profile, prior artifacts, configured tools/skills/outputs and
        persona; returns a tailored approach with per-step rationale + tier and
        tool/skill recommendations. Cached in Redis until the inputs change. Returns
        None on any failure (incl. mock mode) so the caller falls back to the
        deterministic plan and Review never breaks.

        allow_compute=False (persist paths — save_plan, trigger) returns the cached
        plan if one exists but NEVER makes an LLM call (D-109): the ~30s planner runs
        only on the explicit plan DISPLAY, not on every overlay save/trigger."""
        if not getattr(self._settings, "INTELLIGENT_PLANNING", True):
            return None
        from ..agents.schemas import StagePlanIntel

        stack = project.get("tech_stack") or "Node.js + TypeScript"
        profile = self._project_profile(project)
        art_digest = "; ".join(f"{a['type']}:{a['title']}" for a in prior_arts[:20]) or "none"
        outputs = list(stage.get("outputs") or [])
        att_names = [a.get("filename", "") for a in (attachments or [])]
        fw_names = [f.get("name", "") for f in (formworks or [])]
        # Cache signature: recompute only when something that shapes the plan changes.
        sig_src = json.dumps({
            "t": stage["template"], "ov": overlay.get("promptOverlay", ""),
            "ref": overlay.get("referencedArtifactIds", []), "att": overlay.get("attachmentIds", []),
            "fw": overlay.get("formworkIds", []), "stack": stack, "profile": profile,
            "tools": sorted(available_tools), "outputs": sorted(outputs),
            "arts": art_digest, "canon": canon_applied, "persona": stage.get("persona"),
            "attn": sorted(att_names), "fwn": sorted(fw_names), "na": sorted(applicability or {}),
        }, sort_keys=True)
        sig = hashlib.sha256(sig_src.encode()).hexdigest()[:16]
        ckey = f"sdlc:planintel:{project['id']}:{phase}"
        try:
            cached = await self._redis.get(ckey)
            if cached:
                obj = json.loads(cached.decode() if isinstance(cached, (bytes, bytearray)) else cached)
                if obj.get("sig") == sig:
                    return {**obj["plan"], "cached": True}
                # A stale cache (inputs changed) is still useful on a persist path where
                # we won't recompute — show it rather than nothing (D-109).
                if not allow_compute:
                    return {**obj["plan"], "cached": True, "stale": True}
        except Exception:
            pass

        # Persist paths (save/trigger) never pay the ~30s planner cost (D-109): the
        # planner runs only on the explicit plan display.
        if not allow_compute:
            return None

        # Shared build state: if another tab/session is already building THIS plan, wait for
        # its result instead of starting a duplicate ~30s planner call (and let /plan/state
        # report "building" so every view shows the same thing).
        bkey = f"sdlc:planbuild:{project['id']}:{phase}"
        try:
            got = await self._redis.set(bkey, "1", nx=True, ex=180)
        except Exception:  # noqa: BLE001
            got = True
        if not got:
            for _ in range(360):
                await asyncio.sleep(0.5)
                try:
                    cached = await self._redis.get(ckey)
                    if cached:
                        obj = json.loads(cached.decode() if isinstance(cached, (bytes, bytearray)) else cached)
                        if obj.get("sig") == sig:
                            return {**obj["plan"], "cached": True}
                    if not await self._redis.exists(bkey):
                        break
                except Exception:  # noqa: BLE001
                    break
            try:
                await self._redis.set(bkey, "1", nx=True, ex=180)
            except Exception:  # noqa: BLE001
                pass
        try:
            return await self._compute_intelligent_plan(
                ckey=ckey, sig=sig, stage=stage, outputs=outputs, available_tools=available_tools,
                skills=skills, fw_names=fw_names, att_names=att_names, stack=stack, profile=profile,
                art_digest=art_digest, canon_applied=canon_applied, overlay=overlay, applicability=applicability,
            )
        finally:
            try:
                await self._redis.delete(bkey)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------ plan lifecycle
    # draft → building → ready(fresh) → generating. A plan is FRESH only while the inputs it was
    # built for are unchanged; any edit (instructions, references, templates, attachments, trait
    # overrides, upstream outputs) makes it STALE, and generating needs a fresh plan. While a
    # stage is generating, editing and re-planning are locked. All enforced here, not just in the UI.
    @staticmethod
    def _plan_sig(project: dict, row: Any, attachments: list[dict], prior_arts: list[dict],
                  trait_overrides: dict[str, str], step_overrides: dict[str, Any]) -> str:
        from .applicability import strip_scope_block

        src = {
            "ov": strip_scope_block((row["prompt_overlay"] if row else "") or "").strip(),
            "ref": sorted((row["referenced_artifact_ids"] if row else []) or []),
            "fw": sorted((row["formwork_ids"] if row else []) or []),
            "att": sorted(a["id"] for a in attachments),
            "so": step_overrides, "to": trait_overrides,
            "stack": project.get("tech_stack"), "name": project.get("name"),
            "arts": sorted(str(a["id"]) for a in prior_arts),
        }
        return hashlib.sha256(json.dumps(src, sort_keys=True, default=str).encode()).hexdigest()[:20]

    async def _is_generating(self, project_id: str, phase: int) -> bool:
        return bool(await self._redis.exists(f"run:{project_id}:{phase}"))

    async def assert_not_generating(self, project_id: str, phase: int) -> None:
        if await self._is_generating(project_id, phase):
            raise SdlcError("GATE_CONFLICT", "Generation is in progress for this stage — editing is locked until it finishes")

    async def _plan_status(self, project_id: str, phase: int, *, project: dict | None = None) -> dict[str, Any]:
        project = project or await self._db.get_project(project_id)
        row = await self._db.get_stage_plan(project_id, phase)
        attachments = await self._db.list_attachments(project_id, phase)
        prior = [a for a in await self._db.list_artefacts(project_id) if a["phase"] < phase]
        sig = self._plan_sig(project, row, attachments, prior,
                             await self._db.get_trait_overrides(project_id), self._step_overrides(row))
        planned = bool(row and row["plan_sig"])
        generating = await self._is_generating(project_id, phase)
        return {
            "building": bool(await self._redis.exists(f"sdlc:planbuild:{project_id}:{phase}")),
            "ready": bool(await self._redis.exists(f"sdlc:planintel:{project_id}:{phase}")),
            "planned": planned, "stale": planned and row["plan_sig"] != sig,
            "fresh": planned and row["plan_sig"] == sig, "generating": generating, "locked": generating,
            "_sig": sig,
        }

    async def plan_state(self, *, project_id: str, phase: int, user: UserPublic) -> dict[str, Any]:
        """Shared across tabs/sessions: is a plan building / built / stale, is generation running?"""
        await self._authz.assert_project_access(project_id, user)
        st = await self._plan_status(project_id, phase)
        st.pop("_sig", None)
        return st

    async def assert_plan_ready(self, project_id: str, phase: int, user: UserPublic) -> None:
        """Generation gate: only a finished, up-to-date plan may start a run."""
        await self._authz.assert_project_access(project_id, user)
        st = await self._plan_status(project_id, phase)
        if st["generating"]:
            raise SdlcError("GATE_CONFLICT", "This stage is already generating")
        if st["building"]:
            raise SdlcError("GATE_CONFLICT", "The plan is still being built — wait for it to finish")
        if not st["planned"]:
            raise SdlcError("GATE_CONFLICT", "Review the plan before generating")
        if st["stale"]:
            raise SdlcError("GATE_CONFLICT", "Your inputs changed after the plan was reviewed — update the plan before generating")

    async def _compute_intelligent_plan(
        self, *, ckey: str, sig: str, stage: dict, outputs: list[str], available_tools: list[str],
        skills: list[dict], fw_names: list[str], att_names: list[str], stack: str, profile: str,
        art_digest: str, canon_applied: bool, overlay: dict, applicability: dict[str, str] | None,
    ) -> dict[str, Any] | None:
        from ..agents.schemas import StagePlanIntel
        from .prompt_library import render as render_prompt
        sys_p = render_prompt("policy.clarification") + "\n\n" + (
            "You are the planning brain for one stage of an enterprise DevMind delivery pipeline. You do NOT "
            "produce the artifacts — before generation you RECONCILE the user's intent with what THIS "
            "stage can actually do, ADVISE what is best, and let the reviewer decide. Restate what you "
            "understood, then, from the stage's declared OUTPUT ARTIFACTS, recommend which to produce "
            "for THIS request (the user may want only one, e.g. just a PRD — do not force the rest; mark "
            "those recommended=false with a reason). Pick the FORMAT to follow: if the user attached a "
            "document and asked to follow its format, set formatSource to mirror that file's sections; "
            "else a matching formwork; else the stage's default template. List anything out of scope — "
            "including parts of the request that belong to a DIFFERENT stage. Be specific to the inputs; "
            "never generic. Only recommend tools from the AVAILABLE list. One sentence per rationale."
        )
        usr_p = (
            f"STAGE: {stage['name']} (persona: {stage.get('persona')}, template {stage['template']}).\n"
            f"OUTPUT ARTIFACTS this stage can produce: {', '.join(outputs) or '—'}.\n"
            f"AVAILABLE TOOLS (configured — recommend only these): {', '.join(available_tools) or 'none'}.\n"
            f"AVAILABLE SKILLS: {', '.join(s['name'] for s in skills) or 'none'}.\n"
            f"AVAILABLE OUTPUT TEMPLATES (formworks): {', '.join(n for n in fw_names if n) or 'none'}.\n"
            f"ATTACHED DOCUMENTS (user-provided; may define the desired format): {', '.join(n for n in att_names if n) or 'none'}.\n"
            f"TECH STACK: {stack}.\n{profile}\n"
            + ("NOT APPLICABLE to this project (verified from its configuration — mark recommended=false "
               "with this reason, never recommend): "
               + "; ".join(f"{t} ({w})" for t, w in (applicability or {}).items()) + ".\n"
               if applicability else "")
            + "Think about THIS project's type (API/service vs UI app, data store, cloud). Recommend only "
              "artifacts that genuinely apply; list in `suggestedArtifacts` (max 4) anything NOT in the "
              "standard output list that this project would clearly need, each with a one-line reason.\n"
            f"PRIOR-STAGE ARTIFACTS: {art_digest}.\n"
            f"CANON RULES APPLIED: {'yes' if canon_applied else 'no'}.\n\n"
            f"USER INPUT / INSTRUCTIONS for this stage:\n"
            f"{(overlay.get('promptOverlay') or '(none — infer from the stage and context)')[:4000]}\n\n"
            "Produce the proposal: `understood` (restate the intent); `willProduce` (each declared output "
            "with recommended=true/false + a one-line reason, honouring what the user actually asked for); "
            "`formatSource` (default template / an attached file's sections / a formwork); `outOfScope`; a "
            "`recommendation` advising the best course; a short `summary`; the ordered `steps` (generate, "
            "validate, one per recommended tool, gate) with rationale + model tier; `toolRecommendations`; "
            "`skillRecommendations`; `assumptions`; and `risks`."
        )
        try:
            data, _ = await self._deps.llm.generate_json(
                intent="standard", tag=f"stage_planner_t{stage['template']}", temperature=0.1,
                # The proposal (understood/willProduce/format/recommendation) plus steps and
                # tool/skill/assumption/risk lists need headroom; a small cap truncated the
                # JSON on richer stages and failed the whole plan (D-112 fix). Generous and
                # env-tunable via PLAN_MAX_TOKENS. Still one shot (D-109).
                max_tokens=await self._plan_max_tokens(),
                schema=StagePlanIntel, max_attempts=1,
                messages=[{"role": "system", "content": sys_p}, {"role": "user", "content": usr_p}],
            )
            plan = data.model_dump()
        except Exception as err:  # noqa: BLE001 — planning is best-effort; never break Review
            log.info("intelligent planner unavailable (%s); using deterministic plan", err)
            return None
        try:
            await self._redis.set(ckey, json.dumps({"sig": sig, "plan": plan}), ex=3600)
        except Exception:
            pass
        return {**plan, "cached": False}

    async def build_plan(self, *, project_id: str, phase: int, user: UserPublic, run_intel: bool = True) -> dict[str, Any]:
        """The full, editable execution plan for a stage BEFORE generation: the
        typed multi-model steps (each with its resolved model + rationale), the
        selectable model catalog, skills, tools, context inventory and the actual
        system-generated prompt (D-56/D-68). Deterministic — nothing runs, no tokens."""
        await self._authz.assert_project_access(project_id, user)
        wf, stage = await self._stage_for(project_id, phase)
        project = await self._db.get_project(project_id)
        session = await self._db.get_session(project_id)
        st = await self._dynamo.get_phase_state(project_id, phase)
        status = st["status"] if st else "NOT_STARTED"
        # While the stage generates, re-planning is locked: serve the stored plan, compute nothing.
        if run_intel and await self._is_generating(project_id, phase):
            run_intel = False

        row = await self._db.get_stage_plan(project_id, phase)
        overlay = {
            "promptOverlay": row["prompt_overlay"] if row else "",
            "referencedArtifactIds": (row["referenced_artifact_ids"] if row else []) or [],
            "attachmentIds": (row["attachment_ids"] if row else []) or [],
            "formworkIds": (row["formwork_ids"] if row else []) or [],
            "origin": row["origin"] if row else "new",
        }
        # Pending interactive clarification (D-108): the UI renders these as answer cards.
        clarification = None
        if row and row["clarification_json"]:
            try:
                clarification = json.loads(row["clarification_json"])
            except Exception:
                clarification = None
        step_overrides = self._step_overrides(row)
        system, user_prompt, extra_context, context, snippets = await self._assemble_prompt_preview(
            project, session or {}, stage, overlay, lambda e: None
        )
        # Structured multi-model plan (D-68): deterministic, zero-token. Resolve each
        # step's model (auto by tier, or the writer's saved override) from the live roster.
        roster = await self._deps.llm.providers()
        skills = [{"id": s.id, "name": s.name, "tier": s.tier} for s in SKILLS if s.phase in (None, stage["template"])]
        # D-105: only offer tools whose integration is actually configured, so the plan
        # never proposes a tool that can't run.
        available_tools = self._configured_tools(TEMPLATE_TOOLS.get(stage["template"], []))
        prior_for_fit = [a for a in await self._db.list_artefacts(project_id) if a["phase"] < phase]
        fit_attachments = await self._db.list_attachments(project_id, phase)
        fit_upstream = [f"{a['type']} {a['title']}" for a in prior_for_fit[:30]]
        fit_upstream += [a.get("filename", "") for a in fit_attachments]
        fit_upstream.append(self._project_profile(project))
        trait_detail = await self.resolve_project_traits(
            project=project, phase=phase, user_text=overlay.get("promptOverlay", ""),
            upstream=fit_upstream, allow_llm=run_intel)
        not_applicable = inapplicable_types(trait_values({k: v for k, v in trait_detail.items() if k[0] != "_"}),
                                            stage["template"])
        # Plan only what applies: drop tools and outputs the project cannot use.
        available_tools = [t for t in available_tools if TOOL_ARTIFACT.get(t) not in not_applicable]
        steps = derive_plan_steps(
            template=stage["template"], roster=roster, step_overrides=step_overrides,
            outputs=[o for o in (stage.get("outputs") or []) if self._norm_type(o) not in not_applicable],
            skills=skills,
            tools=available_tools,
            external_write_tools=set(EXTERNAL_WRITE_TOOLS),
            gen_prompt_tokens=(len(system) + len(user_prompt)) // 4,
            validation_enabled=getattr(self._settings, "VALIDATION_ENABLED", True),
        )
        deps_keys = stage.get("dependsOn") or []
        by_key = {s["key"]: s for s in wf["stages"]}
        states = {s["SK"]: s for s in await self._dynamo.list_phase_states(project_id)}
        blocked_on = [by_key[d]["name"] for d in deps_keys
                      if (states.get(f"PHASE#{by_key[d]['seq']}") or {}).get("status") != "APPROVED" and d in by_key]
        prior_arts = [a for a in await self._db.list_artefacts(project_id) if a["phase"] < phase]
        if stage.get("iterationId") or stage.get("releaseId"):
            # Sprint/release stages see the foundation + their own sprint (the rest arrives as bounded project memory).
            keep = {s["seq"] for s in wf["stages"] if s.get("scope", "project") == "project"}
            keep |= {s["seq"] for s in wf["stages"]
                     if (stage.get("iterationId") and s.get("iterationId") == stage["iterationId"])
                     or (stage.get("releaseId") and s.get("releaseId") == stage["releaseId"])}
            prior_arts = [a for a in prior_arts if a["phase"] in keep]
        formworks = await self._deps.formworks.list(project_id, user) if self._deps.formworks else []
        attachments = await self._db.list_attachments(project_id, phase)
        canon_applied = bool(await self._deps.canon.render_block(project_id, stage["template"])) if self._deps.canon else False

        # D-105: intelligent, context-aware plan (advisory). None on any failure or in
        # mock mode → the deterministic plan above stands unchanged. D-109: only the
        # explicit plan DISPLAY computes it (run_intel); persist paths reuse the cache.
        # The harness template ALSO derives toolchain artifacts beyond the declared outputs
        # (e.g. UI/API test suites). Plan over everything that would actually be produced,
        # and check each against the project's real traits so none is forced onto a project
        # that cannot use it (an API-only service has no UI to automate).
        excluded = not_applicable
        plan_stage = {**stage, "outputs": [o for o in dict.fromkeys(
            [*(stage.get("outputs") or []), *GENERATED_ARTIFACTS.get(stage["template"], [])])
            if self._norm_type(o) not in excluded]}
        intel = await self._intelligent_plan(
            applicability=excluded, project=project, phase=phase, stage=plan_stage, overlay=overlay,
            available_tools=available_tools, skills=skills, prior_arts=prior_arts, canon_applied=canon_applied,
            attachments=attachments, formworks=formworks, allow_compute=run_intel,
        )
        if intel:
            # Fold the planner's per-step rationale onto the matching deterministic steps.
            rationale_by_id = {s.get("id"): s.get("rationale") for s in intel.get("steps", [])}
            for st_ in steps:
                if rationale_by_id.get(st_["id"]):
                    st_["rationale"] = rationale_by_id[st_["id"]]

        # D-112 fix: the advise→decide surface must ALWAYS render, independent of the
        # LLM planner. When the planner is unavailable (mock mode, disabled, or a
        # truncated/failed call) or returned no proposal, synthesise a deterministic
        # proposal from the stage's declared outputs + attachments so the reviewer can
        # still see what will be produced and choose/skip outputs before triggering.
        det = self._deterministic_proposal(plan_stage, overlay, attachments, formworks)
        if not intel:
            intel = det
        elif not intel.get("willProduce"):
            intel = {
                **intel,
                "willProduce": det["willProduce"],
                "understood": intel.get("understood") or det["understood"],
                "formatSource": intel.get("formatSource") or det["formatSource"],
                "recommendation": intel.get("recommendation") or det["recommendation"],
            }
        intel = self._apply_applicability(intel, excluded)

        if run_intel:  # an explicit plan display = the reviewed plan now matches the saved inputs
            try:
                await self._db.set_stage_plan_sig(project_id, phase, self._plan_sig(
                    project, row, attachments, prior_for_fit, await self._db.get_trait_overrides(project_id),
                    step_overrides))
            except Exception:  # noqa: BLE001
                log.warning("could not record plan signature", exc_info=True)
        plan_state = await self._plan_status(project_id, phase, project=project)
        plan_state.pop("_sig", None)
        return {
            "planState": plan_state,
            "projectId": project_id, "phase": phase, "status": status,
            "canEdit": await self._can_write_stage(project_id, stage, user),
            "blockedOn": blocked_on,
            "stage": {
                "name": stage["name"], "template": stage["template"], "persona": stage["persona"],
                "reviewerRole": stage["reviewerRole"], "writeRoles": self._stage_writers(stage),
                "outputs": stage.get("outputs") or [], "inputs": stage.get("inputs") or [],
            },
            "agent": {
                "persona": stage["persona"], "template": stage["template"],
                "tier": classify_tier(overlay["promptOverlay"] or stage["name"], has_tools=True, context_tokens=0),
                "nodes": PIPELINE_NODES,
            },
            "skills": skills,
            # Pending clarifying questions with options (D-108); null when none.
            "clarification": clarification,
            "expectedTools": available_tools,
            # Multi-model plan (D-68): the typed step list + selectable catalog.
            "steps": steps,
            # Intelligent, context-aware plan (D-105): tailored summary, per-step
            # rationale/tier, tool/skill recommendations, assumptions & risks. Null
            # when disabled or unavailable (deterministic plan stands).
            "intel": intel,
            # What the AI (or a project lead's override) decided the project is — each trait
            # with its source and evidence. Code enforces it; the user can override it.
            "traits": [{"trait": k, **v} for k, v in trait_detail.items()],
            "catalog": build_model_catalog(roster),
            "context": {
                "priorArtifacts": [{"id": a["id"], "phase": a["phase"], "type": a["type"], "title": a["title"]} for a in prior_arts],
                "canonApplied": canon_applied,
                "formworks": [{"id": f["id"], "name": f["name"], "artefactType": f["artefactType"], "scope": f["scope"]} for f in formworks],
                "attachments": [{"id": a["id"], "filename": a["filename"], "isText": a["is_text"]} for a in attachments],
                "ragSnippets": len(snippets),
                "curatedInjectedChars": len(extra_context),
            },
            "overlay": {
                **{k: overlay[k] for k in ("promptOverlay", "referencedArtifactIds", "attachmentIds", "formworkIds", "origin")},
                "stepOverrides": step_overrides,
            },
            "prompt": {"system": system, "user": user_prompt},
        }

    async def save_plan(self, *, project_id: str, phase: int, user: UserPublic, overlay: dict) -> dict[str, Any]:
        """Persist the writer's overlay edits ('Update the plan') and return the
        re-rendered plan preview (D-56). Write-permission required."""
        _, stage = await self._stage_for(project_id, phase)
        if not await self._can_write_stage(project_id, stage, user):
            raise SdlcError("FORBIDDEN", f"Editing the '{stage['name']}' plan requires write permission ({' or '.join(self._stage_writers(stage))})")
        await self.assert_not_generating(project_id, phase)
        row = await self._db.get_stage_plan(project_id, phase)
        await self._db.upsert_stage_plan(
            project_id=project_id, phase=phase, prompt_overlay=overlay.get("promptOverlay", ""),
            referenced_artifact_ids=overlay.get("referencedArtifactIds") or [],
            attachment_ids=overlay.get("attachmentIds") or [],
            formwork_ids=overlay.get("formworkIds") or [],
            step_overrides=overlay.get("stepOverrides") or {},  # per-step model overrides (D-68)
            origin=(row["origin"] if row else "new"), updated_by=user.id,
        )
        self._audit.record(project_id=project_id, phase=phase, agent_role="Orchestrator",
                           event="plan.updated", human_reviewer=user.email, detail={"stage": stage["key"]})
        # D-109: a save is a persist, not a display — don't pay the ~30s planner here;
        # reuse the cached intel. The explicit GET /plan recomputes it when needed.
        return await self.build_plan(project_id=project_id, phase=phase, user=user, run_intel=False)

    async def trigger_stage(self, *, project_id: str, phase: int, user: UserPublic, emit: Emit) -> None:
        """Run ONE stage using its reviewed plan overlay (D-56). Nothing generates
        until this is invoked by a writer; the result goes straight to gate review."""
        emit({"type": "node", "node": "guardrail", "label": "Input guardrail"})
        await self._authz.assert_project_access(project_id, user)
        wf, stage = await self._stage_for(project_id, phase)
        if not await self._can_write_stage(project_id, stage, user):
            raise SdlcError("FORBIDDEN", f"Triggering the '{stage['name']}' stage requires write permission ({' or '.join(self._stage_writers(stage))})")
        await assert_stage_mutable(self._db, stage)
        project = await self._db.get_project(project_id)
        session = await self._db.get_session(project_id)
        if not session:
            raise SdlcError("NOT_FOUND", "Project has no session")

        # Per-part RETRIGGER target (D-107 step 2): the retrigger endpoint stashes the
        # field(s) to regenerate in Redis; consume it here. A retrigger regenerates only
        # those parts (reusing the rest) and is allowed while the stage is PENDING_REVIEW
        # (that is exactly when a reviewer retries a failed part).
        retrigger_fields: list[str] = []
        try:
            _rk = f"sdlc:retrigger:{project_id}:{phase}"
            _raw = await self._redis.get(_rk)
            if _raw:
                await self._redis.delete(_rk)
                retrigger_fields = list(json.loads(_raw.decode() if isinstance(_raw, (bytes, bytearray)) else _raw))
        except Exception:  # noqa: BLE001
            retrigger_fields = []

        resume = False
        try:
            _rsk = f"sdlc:resume:{project_id}:{phase}"
            if await self._redis.get(_rsk):
                await self._redis.delete(_rsk)
                resume = True
        except Exception:  # noqa: BLE001
            resume = False

        states = {s["SK"]: s for s in await self._dynamo.list_phase_states(project_id)}
        status = (states.get(f"PHASE#{phase}") or {}).get("status", "NOT_STARTED")
        if status == "APPROVED" or (status == "PENDING_REVIEW" and not retrigger_fields):
            raise SdlcError("GATE_CONFLICT", f"Stage '{stage['name']}' is {status}; review the current generation before re-triggering")
        by_key = {s["key"]: s for s in wf["stages"]}
        # A stage runs once its declared dependencies are approved. An entry stage
        # (no dependsOn) has none, so it can start immediately — this is what lets a
        # dynamic workflow begin at any phase.
        unmet = [by_key[d]["name"] for d in (stage.get("dependsOn") or [])
                 if d in by_key
                 and (states.get(f"PHASE#{by_key[d]['seq']}") or {}).get("status") != "APPROVED"]
        if unmet:
            raise SdlcError("GATE_CONFLICT", f"Stage '{stage['name']}' is blocked until approved: {', '.join(unmet)}")

        row = await self._db.get_stage_plan(project_id, phase)
        overlay = {
            "promptOverlay": row["prompt_overlay"] if row else "",
            "referencedArtifactIds": (row["referenced_artifact_ids"] if row else []) or [],
            "attachmentIds": (row["attachment_ids"] if row else []) or [],
            "formworkIds": (row["formwork_ids"] if row else []) or [],
        }
        prompt_overlay = overlay["promptOverlay"].strip()
        if prompt_overlay:
            enforce_input(prompt_overlay, channel="plan")
        extra_context = await self._resolve_extra_context(
            project_id, overlay["referencedArtifactIds"], overlay["attachmentIds"], overlay["formworkIds"], emit
        )
        context = [ContextArtifact.model_validate(a) for a in (session.get("context_window") or [])]

        # Interactive ambiguity pre-check (#1/D-108): ask STRUCTURED clarifying
        # questions (with predefined options) instead of assuming — even when a
        # description was given, since a key dimension (e.g. cloud/platform) may still
        # be unspecified. State machine on the stage plan:
        #   - already answered (origin == 'clarification') → skip, generate.
        #   - questions pending (clarification_json set) → re-surface them, stop.
        #   - otherwise → run the check; if it asks, store the questions and stop.
        # Skipped on retrigger.
        already_clarified = bool(row and row["origin"] == "clarification")
        pending_clar = row["clarification_json"] if row else None
        if (getattr(self._settings, "CLARIFY_ENABLED", True) and status == "NOT_STARTED"
                and not retrigger_fields and not already_clarified):
            if pending_clar:
                try:
                    pending_qs = json.loads(pending_clar)
                except Exception:
                    pending_qs = []
                if pending_qs:
                    emit({"type": "clarification", "phase": phase, "stage": stage["name"], "questions": pending_qs})
                    emit({"type": "node", "node": "agent",
                          "label": f"Answer the {len(pending_qs)} clarifying question(s) to proceed"})
                    return
            questions = await self._clarification_questions(
                project=project, stage=stage, user_input=prompt_overlay, context=context, extra_context=extra_context)
            if questions:
                await self._db.set_stage_clarification(project_id, phase, json.dumps(questions))
                self._audit.record(project_id=project_id, phase=phase, agent_role="Orchestrator",
                                   event="clarification.requested", human_reviewer=user.email,
                                   detail={"stage": stage["key"], "count": len(questions)})
                emit({"type": "clarification", "phase": phase, "stage": stage["name"], "questions": questions})
                emit({"type": "node", "node": "agent",
                      "label": f"Clarification needed — {len(questions)} question(s); answer them to proceed"})
                return

        set_run_context(project_id, phase)
        emit({"type": "session", "projectId": project_id, "sessionId": session["id"], "phase": phase})
        emit({"type": "node", "node": "executor", "label": f"Triggering reviewed plan — {stage['name']}"})

        trig_traits = await self.resolve_project_traits(
            project=project, phase=phase, user_text=prompt_overlay,
            upstream=[f"{a.type} {a.title}" for a in context[-30:]] + [self._project_profile(project)],
            allow_llm=False)
        stage_context, stage_extra = list(context), extra_context
        if self.lifecycle is not None and (stage.get("iterationId") or stage.get("releaseId")):
            stage_context, agile_extra = await self.lifecycle.context_for(project_id, stage, context)
            stage_extra = (extra_context + "\n\n" + agile_extra).strip()
        state = AgentState(
            project_traits=trait_values({k: v for k, v in trig_traits.items() if k[0] != "_"}),
            project_id=project_id, session_id=session["id"], current_phase=phase,
            stage_template=stage["template"], stage_name=stage["name"], stage_reviewer=stage["reviewerRole"],
            user_input=prompt_overlay or f"Generate {', '.join(stage.get('outputs') or [])} for '{stage['name']}'.",
            context_window=list(stage_context), amend_comments=None,
            tech_stack=project.get("tech_stack") or "Node.js + TypeScript",
            project_profile=self._project_profile(project),
            has_codebase=(await self._db.count_codebase_files(project_id)) > 0, extra_context=stage_extra,
            model_overrides=self._model_overrides_from(self._step_overrides(row)),  # per-step model (D-68)
            # Retrigger implies the split (parts only exist under it); force it on then.
            per_artifact=(await self._per_artifact_enabled()) or bool(retrigger_fields) or resume,  # D-106
            retrigger_fields=retrigger_fields,  # D-107 step 2: regenerate only these parts
            resume=resume,  # continue an interrupted run from its persisted parts
            **self._custom_fields(stage),  # custom phase config (D-74)
        )
        await self._dynamo.put_phase_state(project_id=project_id, phase=phase, status="IN_PROGRESS", reviewer_role=stage["reviewerRole"])
        summary = await self._status_summary(project_id, project["name"], wf, phase)
        final_state, phase_result = await self._run_stage_pipeline(
            state, project_id=project_id, seq=phase, reviewer_role=stage["reviewerRole"],
            prev_status=status, emit=emit, summary=summary,
        )

        last_gate = final_state.gate_status
        # Did the run settle into a recognized state? If not, we must not leave the
        # phase stuck on IN_PROGRESS (D-111). PENDING_REVIEW is the normal outcome;
        # IN_PROGRESS (phase-6 build loop), ESCALATED and AMEND_REQUESTED are their
        # own legitimate flows driven elsewhere.
        settled = last_gate in ("IN_PROGRESS", "ESCALATED", "AMEND_REQUESTED")
        if phase_result:
            await self._db.update_context_window(session["id"], final_state.context_window)
            # Persist the deferred external-write plan for this stage (D-67).
            await self._persist_publish_plan(project_id, phase, phase_result)
            if final_state.gate_status == "PENDING_REVIEW":
                await self._dynamo.put_phase_state(project_id=project_id, phase=phase, status="PENDING_REVIEW", reviewer_role=stage["reviewerRole"])
                emit({"type": "gate", "phase": phase, "status": "PENDING_REVIEW", "reviewerRole": stage["reviewerRole"]})
                self._audit.record(project_id=project_id, phase=phase, agent_role=stage["persona"],
                                   event="gate.pending_review", human_reviewer=user.email,
                                   detail={"reviewerRole": stage["reviewerRole"], "stage": stage["key"], "viaPlan": True})
                await self._after_review_ready(project_id, phase, final_state, user.email)
                settled = True
            # Impact propagation (#): a re-run (retrigger/amend) just produced a new
            # version of this stage's outputs, so downstream stages that already
            # consumed the old ones are now potentially stale. Flag them (advisory —
            # statuses untouched) so the reviewer can decide whether to regenerate.
            origin = (row["origin"] if row and "origin" in row else None)
            if origin in ("retrigger", "amend"):
                await self._flag_downstream_stale(project_id, phase, wf, states, stage["name"])
            await self._db.delete_stage_plan(project_id, phase)  # draft consumed

        # Safety net (D-111): a run that produced no reviewable output (no result, or a
        # gate state we didn't settle) must NOT leave the stage stuck on "Generating".
        # Reset it to NOT_STARTED so it is immediately re-triggerable, and say so.
        if not settled:
            await self._dynamo.put_phase_state(project_id=project_id, phase=phase, status="NOT_STARTED", reviewer_role=stage["reviewerRole"])
            self._audit.record(project_id=project_id, phase=phase, agent_role="Orchestrator",
                               event="stage.no_output", detail={"stage": stage["key"], "gate": last_gate})
            emit({"type": "node", "node": "guardrail", "status": "error",
                  "label": "Stage produced no reviewable output — reset so you can re-trigger it."})

        safe_response, masked = sanitise_output(final_state.final_response)
        if masked:
            self._audit.record(project_id=project_id, phase=phase, agent_role="OutputGuardrail",
                               event="guardrail.output_masked", detail={"rules": masked})
        turn_msg = f"▶ Plan triggered — {stage['name']}" + (f"\n\nInstructions: {prompt_overlay}" if prompt_overlay else "")
        await self._db.insert_chat_turn(session["id"], phase, turn_msg, safe_response)
        emit({"type": "done", "finalResponse": safe_response, "phase": phase, "gateStatus": last_gate})

    async def _after_review_ready(self, project_id: str, phase: int, state: Any, actor: str) -> None:
        """The stage just reached PENDING_REVIEW: let the agile lifecycle react (index staging, proposals) and
        give an `auto` gate its chance. Never allowed to fail the generation that already succeeded."""
        if self.lifecycle is None:
            return
        try:
            await self.lifecycle.after_review_ready(
                project_id, phase, provider=getattr(state, "last_provider", "") or "",
                model=getattr(state, "last_model", "") or "", actor=actor)
        except Exception:  # noqa: BLE001
            log.exception("after-review lifecycle failed project=%s phase=%s", project_id, phase)

    async def _flag_downstream_stale(
        self, project_id: str, phase: int, wf: dict, states: dict, stage_name: str,
    ) -> list[int]:
        """Mark every downstream stage that already consumed this stage's output as
        stale after a re-run (impact propagation). Non-destructive — statuses are
        preserved. Returns affected seqs."""
        downstream = transitive_downstream_seqs(wf["stages"], phase)
        affected: list[int] = []
        reason = f"Upstream stage '{stage_name}' was re-generated"
        for seq in downstream:
            status = (states.get(f"PHASE#{seq}") or {}).get("status", "NOT_STARTED")
            if status in STALE_STATUSES:
                await self._dynamo.mark_phase_stale(
                    project_id=project_id, phase=seq, reason=reason, source_phase=phase,
                )
                affected.append(seq)
        if affected:
            self._audit.record(
                project_id=project_id, phase=phase, agent_role="Orchestrator",
                event="downstream.stale_flagged",
                detail={"reason": reason, "affected": affected, "sourcePhase": phase},
            )
        return affected

    async def _resolve_extra_context(
        self, project_id: str, referenced_artifact_ids: list[str],
        attachment_ids: list[str], formwork_ids: list[str], emit: Emit,
    ) -> str:
        """Render the user's curated @references + attachments into one labelled
        block (D-54). Each item is capped and the whole block bounded so a large
        attachment can't blow the free-tier token budget; the verbatim originals
        remain in the content store."""
        per_item, total_cap = 8_000, 24_000
        parts: list[str] = []

        async def _body(row) -> str:  # noqa: ANN001
            content = row["content"] or ""
            if row["storage_key"]:
                stored = await self._deps.content.get(row["storage_key"])
                if stored is not None:
                    content = stored
            return content

        if referenced_artifact_ids:
            rows = {r["id"]: r for r in await self._db.get_artefacts_by_ids(referenced_artifact_ids)}
            for aid in referenced_artifact_ids:  # preserve the user's order
                row = rows.get(aid)
                if not row or row["project_id"] != project_id:
                    continue
                body = (await _body(row))[:per_item]
                parts.append(f"### Reference — [Phase {row['phase']}] {row['type']}: {row['title']}\n{body}")

        if attachment_ids:
            rows = {r["id"]: r for r in await self._db.get_attachments_by_ids(attachment_ids)}
            for aid in attachment_ids:
                row = rows.get(aid)
                if not row or row["project_id"] != project_id:
                    continue
                if not row["is_text"]:
                    parts.append(f"### Attachment — {row['filename']} (binary; not inlined)")
                    continue
                body = (await self._deps.content.get(row["storage_key"]) or "")[:per_item]
                parts.append(f"### Attachment — {row['filename']}\n{body}")

        if formwork_ids:
            rows = {r["id"]: r for r in await self._db.get_formworks_by_ids(formwork_ids)}
            for fid in formwork_ids:
                row = rows.get(fid)
                # Platform templates (project_id NULL) are shareable across projects.
                if not row or (row["project_id"] not in (None, project_id)):
                    continue
                parts.append(f"### Template — {row['name']}\n{(row['template'] or '')[:per_item]}")

        if not parts:
            return ""
        emit({"type": "node", "node": "agent",
              "label": f"Attached context: {len(parts)} item(s) (references + files)"})
        block = "\n\n".join(parts)
        return block[:total_cap] + ("\n… (attached context truncated)" if len(block) > total_cap else "")

    async def _load_or_create(self, project_id: str | None, message: str, user: UserPublic):
        if project_id:
            await self._authz.assert_project_access(project_id, user)
            project = await self._db.get_project(project_id)
            if not project:
                raise SdlcError("NOT_FOUND", f"Project {project_id} not found")
            session = await self._db.get_session(project_id)
            if not session:
                raise SdlcError("NOT_FOUND", f"Project {project_id} has no session")
            if session["current_phase"] != project["current_phase"]:
                await self._db.set_project_phase(project_id, project["current_phase"], project["status"])
                session = await self._db.get_session(project_id)
            return project, session

        self._authz.assert_can_create_project(user)
        name = (message.splitlines()[0] if message else "New Project")[:80]
        project = await self._db.create_project(name=name, created_by=user.id)
        session = await self._db.get_session(project["id"])
        self._audit.record(
            project_id=project["id"], phase=1, agent_role="Orchestrator",
            event="project.created", human_reviewer=user.email, detail={"name": name, "via": "chat"},
        )
        return project, session

    async def _status_summary(self, project_id: str, name: str, wf: dict, current_phase: int) -> str:
        states = {s["SK"]: s for s in await self._dynamo.list_phase_states(project_id)}
        rows = []
        for stage in wf["stages"]:
            st = states.get(f"PHASE#{stage['seq']}")
            marker = " ← current" if stage["seq"] == current_phase else ""
            parallel = f" (∥ level {stage['level'] + 1})"
            rows.append(f"| {stage['seq']} | {stage['name']}{parallel} | "
                        f"{st['status'] if st else 'NOT_STARTED'} | {stage['reviewerRole']} |{marker}")
        return (f"## Project status — {name}\n\n| Stage | Name | Status | Gate reviewer |\n"
                f"|---|---|---|---|\n" + "\n".join(rows))


async def sse_stream(handler: Any) -> AsyncIterator[str]:
    """Adapter: pump emit() events from the chat pipeline into an SSE byte stream."""
    import asyncio

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    def emit(event: dict[str, Any]) -> None:
        queue.put_nowait(event)

    async def _run() -> None:
        try:
            await handler(emit)
        except SdlcError as err:
            queue.put_nowait({"type": "error", "code": err.code, "message": err.message})
        except Exception as err:
            log.exception("chat pipeline error")
            queue.put_nowait({"type": "error", "code": "INTERNAL", "message": f"Pipeline failed: {err}"})
        finally:
            queue.put_nowait(None)

    task = asyncio.get_running_loop().create_task(_run())
    yield ":ok\n\n"
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            # Compact separators: byte-parity with the previous emitter's JSON.
            yield f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
    finally:
        task.cancel()


# --- Background stage generation (D-97, Level 1) -----------------------------
# A stage run must NOT die because the browser navigated away. We run the pipeline
# as a DETACHED task that is not cancelled when the SSE connection closes; the run
# finishes server-side and its persisted status/artifacts reflect the result. A
# concurrency guard rejects a second trigger for the same stage while one is live.
# (Level 2 will make this durable across restarts with a jobs table + Redis progress.)
_bg_runs: dict[str, Any] = {}


def is_stage_running(key: str) -> bool:
    task = _bg_runs.get(key)
    return task is not None and not task.done()


async def sse_stream_bg(handler: Any, key: str) -> AsyncIterator[str]:
    """Like sse_stream, but the run is detached: closing the client stream does NOT
    cancel generation. Events emitted after a disconnect are simply dropped."""
    import asyncio

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    def emit(event: dict[str, Any]) -> None:
        queue.put_nowait(event)

    async def _run() -> None:
        try:
            await handler(emit)
        except SdlcError as err:
            queue.put_nowait({"type": "error", "code": err.code, "message": err.message})
        except Exception as err:
            log.exception("chat pipeline error (background)")
            queue.put_nowait({"type": "error", "code": "INTERNAL", "message": f"Pipeline failed: {err}"})
        finally:
            queue.put_nowait(None)
            _bg_runs.pop(key, None)

    task = asyncio.get_running_loop().create_task(_run())
    _bg_runs[key] = task
    yield ":ok\n\n"
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
    finally:
        # D-97: deliberately DO NOT cancel `task` — let generation run to completion
        # server-side even though this viewer disconnected. The task de-registers
        # itself on finish; the stage's persisted state is the source of truth.
        pass
