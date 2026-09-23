"""POST /api/chat lifecycle (Module 2 §1): input guardrail → session load →
LangGraph invocation → output guardrail → persistence → audit → SSE stream."""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Callable

from redis.asyncio import Redis

from ..agents.phase_agents import EXTERNAL_WRITE_TOOLS, TEMPLATE_TOOLS, AgentDeps
from ..services.plan_model import build_model_catalog, derive_plan_steps
from ..agents.prompts import build_phase_prompt
from ..config import Settings
from ..domain.errors import SdlcError
from ..domain.models import AgentState, ContextArtifact, UserPublic
from ..graph.pipeline import PIPELINE_NODES, build_pipeline, run_pipeline
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
        self._publisher = publisher # PublishService: stores deferred publish plans
        self._pipeline = build_pipeline()

    async def _run_stage_pipeline(
        self, state: AgentState, *, project_id: str, seq: int, reviewer_role: str,
        prev_status: str, emit: Emit, summary: str,
    ):
        """Run one stage through the pipeline with failure recovery: if
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

    async def _persist_publish_plan(self, project_id: str, seq: int, phase_result: Any) -> None:
        """Store the phase's deferred external-write plan so it can be replayed on
        gate approval. Replaces any prior plan for the phase."""
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

        # Dynamic workflow: find the level (parallel group) holding the
        # current stage slot; a single run generates EVERY ready stage in it.
        wf = await self._workflow.view(project["id"])
        stages_by_seq = {s["seq"]: s for s in wf["stages"]}
        level_seqs = next((seqs for seqs in wf["levels"] if phase_id in seqs), wf["levels"][0])
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
        # Rich compose: resolve the user's curated @references + attachments
        # into one labelled block, injected into every stage run of this turn.
        extra_context = await self._resolve_extra_context(
            project["id"], referenced_artifact_ids or [], attachment_ids or [], formwork_ids or [], emit
        )
        responses: list[str] = []
        last_gate = "IN_PROGRESS"
        # The reviewer feedback that drove this run (if it's an amend
        # regeneration), so the chat history records what was actually requested
        # instead of the generic internal trigger message (transparency).
        amend_feedback: str | None = None

        if len(ready) > 1:
            emit({"type": "node", "node": "executor",
                  "label": f"Parallel group: running {len(ready)} stages of this level"})

        for stage in ready:
            seq = stage["seq"]
            set_run_context(project["id"], seq) # attribute LLM/tool spans
            st = states.get(f"PHASE#{seq}")
            amend = st.get("comments") if st and st.get("status") == "AMEND_REQUESTED" else None
            if amend:
                amend_feedback = amend

            sp_row = await self._db.get_stage_plan(project["id"], seq)
            state = AgentState(
                project_id=project["id"], session_id=session["id"], current_phase=seq,
                stage_template=stage["template"], stage_name=stage["name"],
                stage_reviewer=stage["reviewerRole"],
                user_input=message, context_window=list(context), amend_comments=amend,
                tech_stack=project.get("tech_stack") or "Node.js + TypeScript",
                project_profile=self._project_profile(project),
                has_codebase=has_codebase, extra_context=extra_context,
                model_overrides=self._model_overrides_from(self._step_overrides(sp_row)), # per-step model
                **self._custom_fields(stage), # custom phase config
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
                # Persist the deferred external-write plan for this stage.
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
        # requested.
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
        """Viz: what WOULD run on the next chat turn — the ready stages of
        the current level with their plan steps, model tier, expected MCP tools
        and stage-scoped skills — without executing anything."""
        await self._authz.assert_project_access(project_id, user)
        project = await self._db.get_project(project_id)
        if not project:
            raise SdlcError("NOT_FOUND", f"Project {project_id} not found")
        phase_id = int(project["current_phase"])

        wf = await self._workflow.view(project_id)
        stages_by_seq = {s["seq"]: s for s in wf["stages"]}
        level_seqs = next((seqs for seqs in wf["levels"] if phase_id in seqs), wf["levels"][0])
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

    # ------------------------------------------------------------ Plan Review & Edit gate
    async def _stage_for(self, project_id: str, phase: int) -> tuple[dict, dict]:
        wf = await self._workflow.view(project_id)
        stage = next((s for s in wf["stages"] if s["seq"] == phase), None)
        if not stage:
            raise SdlcError("NOT_FOUND", f"No stage at position {phase} in this workflow")
        return wf, stage

    def _stage_writers(self, stage: dict) -> list[str]:
        return stage.get("writeRoles") or stage.get("team") or [stage["reviewerRole"]]

    async def can_write_stage(self, project_id: str, phase: int, user: UserPublic) -> bool:
        """Public wrapper that resolves the stage by position."""
        _, stage = await self._stage_for(project_id, phase)
        return await self._can_write_stage(project_id, stage, user)

    async def _can_write_stage(self, project_id: str, stage: dict, user: UserPublic) -> bool:
        if user.role == "SUPER_ADMIN":
            return True
        project = await self._db.get_project(project_id)
        if user.role == "PROJECT_MANAGER" and project and project["created_by"] == user.id:
            return True
        membership = await self._authz.get_membership_role(project_id, user.id)
        return membership in self._stage_writers(stage)

    async def _assemble_prompt_preview(self, project: dict, session: dict, stage: dict, overlay: dict, emit: Emit):
        """Assemble the exact system+user prompt the stage would run with, given the
        editable overlay — WITHOUT calling the LLM. The proprietary craft/quality-bar
        core is included read-only; only the overlay (instructions + curated context)
        is user-editable."""
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
            # Custom phase: preview the generic prompt the runner will use —
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
        """Read persisted per-step model overrides, tolerating a JSONB value
        that decodes as either a dict or a JSON string."""
        if not row:
            return {}
        raw = row["step_overrides"] if "step_overrides" in row else {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw or "{}")
            except json.JSONDecodeError:
                raw = {}
        return raw or {}

    @staticmethod
    def _project_profile(project: dict) -> str:
        """Compact project profile threaded into every stage: name, tech
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
    ) -> list[str]:
        """Ambiguity pre-check: return clarifying questions when the inputs are
        too ambiguous to generate without assuming; [] to proceed. Never raises —
        a failed check must not block generation."""
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
            return [f"Please provide: {b}" for b in base][:max_questions]
        digest = "\n".join(f"- [P{a.phase}] {a.type}: {a.title}" for a in context[-20:]) or "(no upstream artifacts yet)"
        if extra_context:
            digest = f"{digest}\n\nCurated context:\n{extra_context[:2000]}"
        req = user_input.strip() or f"Produce {', '.join(stage.get('outputs') or ['the deliverables'])} for the '{stage['name']}' stage."
        try:
            out, _ = await self._deps.llm.generate_json(
                intent="standard", tier="light", tag="clarify", max_tokens=512,
                schema=ClarificationOutput,
                messages=[
                    {"role": "system", "content": render_prompt(
                        "clarify.system", persona=persona, stage_name=stage["name"],
                        max_questions=max_questions,
                        mandatory_inputs=mandatory_block)},
                    {"role": "user", "content": render_prompt(
                        "clarify.user", request=req, project_profile=self._project_profile(project),
                        context_digest=digest)},
                ],
            )
        except Exception:
            return []
        return list(out.questions)[:max_questions] if out.needs_clarification else []

    @staticmethod
    def _custom_fields(stage: dict) -> dict[str, Any]:
        """Custom-phase config → AgentState fields. Only for template 7; the
        built-in engines ignore these."""
        if stage.get("template") != 7:
            return {}
        return {
            "custom_persona": stage.get("persona", "") or "",
            "custom_prompt_id": stage.get("promptId", "") or "",
            "custom_outputs": list(stage.get("outputs") or []),
            "custom_tools": list(stage.get("tools") or []),
        }

    @staticmethod
    def _model_overrides_from(step_overrides: dict[str, Any]) -> dict[str, str]:
        """Flatten persisted step overrides to { stepId: 'provider/model' } for the
        run, keeping only entries that actually pin a model."""
        out: dict[str, str] = {}
        for step_id, entry in (step_overrides or {}).items():
            model = entry.get("model") if isinstance(entry, dict) else None
            if model:
                out[step_id] = model
        return out

    async def build_plan(self, *, project_id: str, phase: int, user: UserPublic) -> dict[str, Any]:
        """The full, editable execution plan for a stage BEFORE generation: the
        typed multi-model steps (each with its resolved model + rationale), the
        selectable model catalog, skills, tools, context inventory and the actual
        system-generated prompt. Deterministic — nothing runs, no tokens."""
        await self._authz.assert_project_access(project_id, user)
        wf, stage = await self._stage_for(project_id, phase)
        project = await self._db.get_project(project_id)
        session = await self._db.get_session(project_id)
        st = await self._dynamo.get_phase_state(project_id, phase)
        status = st["status"] if st else "NOT_STARTED"

        row = await self._db.get_stage_plan(project_id, phase)
        overlay = {
            "promptOverlay": row["prompt_overlay"] if row else "",
            "referencedArtifactIds": (row["referenced_artifact_ids"] if row else []) or [],
            "attachmentIds": (row["attachment_ids"] if row else []) or [],
            "formworkIds": (row["formwork_ids"] if row else []) or [],
            "origin": row["origin"] if row else "new",
        }
        step_overrides = self._step_overrides(row)
        system, user_prompt, extra_context, context, snippets = await self._assemble_prompt_preview(
            project, session or {}, stage, overlay, lambda e: None
        )
        # Structured multi-model plan: deterministic, zero-token. Resolve each
        # step's model (auto by tier, or the writer's saved override) from the live roster.
        roster = await self._deps.llm.providers()
        skills = [{"id": s.id, "name": s.name, "tier": s.tier} for s in SKILLS if s.phase in (None, stage["template"])]
        steps = derive_plan_steps(
            template=stage["template"], roster=roster, step_overrides=step_overrides,
            outputs=list(stage.get("outputs") or []), skills=skills,
            tools=TEMPLATE_TOOLS.get(stage["template"], []),
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
        formworks = await self._deps.formworks.list(project_id, user) if self._deps.formworks else []
        attachments = await self._db.list_attachments(project_id, phase)

        return {
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
            "expectedTools": TEMPLATE_TOOLS.get(stage["template"], []),
            # Multi-model plan: the typed step list + selectable catalog.
            "steps": steps,
            "catalog": build_model_catalog(roster),
            "context": {
                "priorArtifacts": [{"id": a["id"], "phase": a["phase"], "type": a["type"], "title": a["title"]} for a in prior_arts],
                "canonApplied": bool(await self._deps.canon.render_block(project_id, stage["template"])) if self._deps.canon else False,
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
        re-rendered plan preview. Write-permission required."""
        _, stage = await self._stage_for(project_id, phase)
        if not await self._can_write_stage(project_id, stage, user):
            raise SdlcError("FORBIDDEN", f"Editing the '{stage['name']}' plan requires write permission ({' or '.join(self._stage_writers(stage))})")
        row = await self._db.get_stage_plan(project_id, phase)
        await self._db.upsert_stage_plan(
            project_id=project_id, phase=phase, prompt_overlay=overlay.get("promptOverlay", ""),
            referenced_artifact_ids=overlay.get("referencedArtifactIds") or [],
            attachment_ids=overlay.get("attachmentIds") or [],
            formwork_ids=overlay.get("formworkIds") or [],
            step_overrides=overlay.get("stepOverrides") or {}, # per-step model overrides
            origin=(row["origin"] if row else "new"), updated_by=user.id,
        )
        self._audit.record(project_id=project_id, phase=phase, agent_role="Orchestrator",
                           event="plan.updated", human_reviewer=user.email, detail={"stage": stage["key"]})
        return await self.build_plan(project_id=project_id, phase=phase, user=user)

    async def trigger_stage(self, *, project_id: str, phase: int, user: UserPublic, emit: Emit) -> None:
        """Run ONE stage using its reviewed plan overlay. Nothing generates
        until this is invoked by a writer; the result goes straight to gate review."""
        emit({"type": "node", "node": "guardrail", "label": "Input guardrail"})
        await self._authz.assert_project_access(project_id, user)
        wf, stage = await self._stage_for(project_id, phase)
        if not await self._can_write_stage(project_id, stage, user):
            raise SdlcError("FORBIDDEN", f"Triggering the '{stage['name']}' stage requires write permission ({' or '.join(self._stage_writers(stage))})")
        project = await self._db.get_project(project_id)
        session = await self._db.get_session(project_id)
        if not session:
            raise SdlcError("NOT_FOUND", "Project has no session")

        states = {s["SK"]: s for s in await self._dynamo.list_phase_states(project_id)}
        status = (states.get(f"PHASE#{phase}") or {}).get("status", "NOT_STARTED")
        if status in ("APPROVED", "PENDING_REVIEW"):
            raise SdlcError("GATE_CONFLICT", f"Stage '{stage['name']}' is {status}; review the current generation before re-triggering")
        by_key = {s["key"]: s for s in wf["stages"]}
        # Optional dependencies don't block (they may be skipped when starting
        # mid-pipeline); only required upstream gates must be APPROVED first.
        unmet = [by_key[d]["name"] for d in (stage.get("dependsOn") or [])
                 if d in by_key and not by_key[d].get("optional")
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

        # Ambiguity pre-check: on a fresh, un-curated trigger, ask clarifying
        # questions instead of assuming. Questions are written into the plan overlay
        # so the reviewer answers them in Plan Review, then re-runs; once the overlay
        # is non-empty the check is skipped and generation proceeds.
        if getattr(self._settings, "CLARIFY_ENABLED", True) and not prompt_overlay and status == "NOT_STARTED":
            questions = await self._clarification_questions(
                project=project, stage=stage, user_input=prompt_overlay, context=context, extra_context=extra_context)
            if questions:
                block = ("## Clarifying questions\n\nThese inputs look ambiguous. Please answer inline, then re-run this "
                         "stage — your answers become the stage's plan and guide generation:\n\n"
                         + "\n".join(f"{i + 1}. {q}\n   - Answer: " for i, q in enumerate(questions)))
                await self._db.upsert_stage_plan(
                    project_id=project_id, phase=phase, prompt_overlay=block,
                    referenced_artifact_ids=overlay["referencedArtifactIds"],
                    attachment_ids=overlay["attachmentIds"], formwork_ids=overlay["formworkIds"],
                    origin="clarification", updated_by=user.email,
                )
                self._audit.record(project_id=project_id, phase=phase, agent_role="Orchestrator",
                                   event="clarification.requested", human_reviewer=user.email,
                                   detail={"stage": stage["key"], "questions": questions})
                emit({"type": "clarification", "phase": phase, "stage": stage["name"], "questions": questions})
                emit({"type": "node", "node": "agent",
                      "label": f"Clarification needed — {len(questions)} question(s) written to the plan; answer and re-run"})
                return

        set_run_context(project_id, phase)
        emit({"type": "session", "projectId": project_id, "sessionId": session["id"], "phase": phase})
        emit({"type": "node", "node": "executor", "label": f"Triggering reviewed plan — {stage['name']}"})

        state = AgentState(
            project_id=project_id, session_id=session["id"], current_phase=phase,
            stage_template=stage["template"], stage_name=stage["name"], stage_reviewer=stage["reviewerRole"],
            user_input=prompt_overlay or f"Generate {', '.join(stage.get('outputs') or [])} for '{stage['name']}'.",
            context_window=list(context), amend_comments=None,
            tech_stack=project.get("tech_stack") or "Node.js + TypeScript",
            project_profile=self._project_profile(project),
            has_codebase=(await self._db.count_codebase_files(project_id)) > 0, extra_context=extra_context,
            model_overrides=self._model_overrides_from(self._step_overrides(row)), # per-step model
            **self._custom_fields(stage), # custom phase config
        )
        await self._dynamo.put_phase_state(project_id=project_id, phase=phase, status="IN_PROGRESS", reviewer_role=stage["reviewerRole"])
        summary = await self._status_summary(project_id, project["name"], wf, phase)
        final_state, phase_result = await self._run_stage_pipeline(
            state, project_id=project_id, seq=phase, reviewer_role=stage["reviewerRole"],
            prev_status=status, emit=emit, summary=summary,
        )

        last_gate = final_state.gate_status
        if phase_result:
            await self._db.update_context_window(session["id"], final_state.context_window)
            # Persist the deferred external-write plan for this stage.
            await self._persist_publish_plan(project_id, phase, phase_result)
            if final_state.gate_status == "PENDING_REVIEW":
                await self._dynamo.put_phase_state(project_id=project_id, phase=phase, status="PENDING_REVIEW", reviewer_role=stage["reviewerRole"])
                emit({"type": "gate", "phase": phase, "status": "PENDING_REVIEW", "reviewerRole": stage["reviewerRole"]})
                self._audit.record(project_id=project_id, phase=phase, agent_role=stage["persona"],
                                   event="gate.pending_review", human_reviewer=user.email,
                                   detail={"reviewerRole": stage["reviewerRole"], "stage": stage["key"], "viaPlan": True})
            # Impact propagation (#): a re-run (retrigger/amend) just produced a new
            # version of this stage's outputs, so downstream stages that already
            # consumed the old ones are now potentially stale. Flag them (advisory —
            # statuses untouched) so the reviewer can decide whether to regenerate.
            origin = (row["origin"] if row and "origin" in row else None)
            if origin in ("retrigger", "amend"):
                await self._flag_downstream_stale(project_id, phase, wf, states, stage["name"])
            await self._db.delete_stage_plan(project_id, phase)  # draft consumed

        safe_response, masked = sanitise_output(final_state.final_response)
        if masked:
            self._audit.record(project_id=project_id, phase=phase, agent_role="OutputGuardrail",
                               event="guardrail.output_masked", detail={"rules": masked})
        turn_msg = f"▶ Plan triggered — {stage['name']}" + (f"\n\nInstructions: {prompt_overlay}" if prompt_overlay else "")
        await self._db.insert_chat_turn(session["id"], phase, turn_msg, safe_response)
        emit({"type": "done", "finalResponse": safe_response, "phase": phase, "gateStatus": last_gate})

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
        block. Each item is capped and the whole block bounded so a large
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
