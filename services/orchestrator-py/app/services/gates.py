"""Gate Controller: RBAC-checked human sign-off over
the project's DYNAMIC workflow. Stages in the same derived level form a
parallel group — their gates are independent, and the project advances to the
next level only when every gate in the current level is APPROVED."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Awaitable, Callable

from ..domain.errors import SdlcError
from ..domain.models import PhaseStateView, UserPublic
from ..repos.aws import DynamoStore
from ..repos.pg import Database
from .audit import AuditService
from .authz import AuthzService
from .guardrails import enforce_input

log = logging.getLogger("gates")

Regenerate = Callable[[str, int, UserPublic], Awaitable[None]]


class GateService:
    def __init__(
        self, db: Database, dynamo: DynamoStore, audit: AuditService,
        authz: AuthzService, workflow: Any, regenerate: Regenerate, publisher: Any = None,
    ) -> None:
        self._db = db
        self._dynamo = dynamo
        self._audit = audit
        self._authz = authz
        self._workflow = workflow
        self._regenerate = regenerate
        self._publisher = publisher # PublishService; None disables deferred publish

    async def list_states(self, project_id: str, viewer: UserPublic | None = None) -> list[PhaseStateView]:
        wf = await self._workflow.view(project_id)
        states = {s["SK"]: s for s in await self._dynamo.list_phase_states(project_id)}
        membership = None
        if viewer and viewer.role not in ("SUPER_ADMIN", "PROJECT_MANAGER"):
            membership = await self._authz.get_membership_role(project_id, viewer.id)
        views: list[PhaseStateView] = []
        for stage in wf["stages"]:
            st = states.get(f"PHASE#{stage['seq']}")
            status = st["status"] if st else "NOT_STARTED"
            # A stage may name several gate reviewers; any of them can sign.
            reviewers = stage.get("reviewerRoles") or [stage["reviewerRole"]]
            can_review = (
                status == "PENDING_REVIEW"
                and viewer is not None
                and (viewer.role == "SUPER_ADMIN" or membership in reviewers)
            )
            views.append(PhaseStateView(
                phase=stage["seq"], name=stage["name"], status=status,
                reviewerRole=stage["reviewerRole"],
                updatedAt=st["updatedAt"] if st else "",
                reviewedBy=st.get("reviewedBy") if st else None,
                canReview=can_review,
                stale=bool(st.get("stale")) if st else False,
                staleReason=st.get("staleReason") if st else None,
                requiredReviewers=reviewers,
                signedOff=[],  # per-user/artifact sign-off detail comes from the signoffs endpoint
            ))
        return views

    async def review(
        self, *, project_id: str, phase: int, decision: str,
        comments: str | None, user: UserPublic,
    ) -> dict[str, Any]:
        wf = await self._workflow.view(project_id)
        stage = next((s for s in wf["stages"] if s["seq"] == phase), None)
        if not stage:
            raise SdlcError("NOT_FOUND", f"No stage at position {phase} in this workflow")
        # Resolve the required reviewer USERS (default = project members whose role is
        # a stage reviewer role; the PM can override with an explicit reviewerUsers
        # list). Every required user must sign off every artifact before completion.
        required_users = await self._required_reviewer_users(project_id, stage)
        project = await self._db.get_project(project_id)
        override = user.role == "SUPER_ADMIN" or (
            user.role == "PROJECT_MANAGER" and project and project["created_by"] == user.id
        )
        if not override and user.email.lower() not in required_users:
            who = ", ".join(sorted(required_users)) or "an assigned reviewer"
            raise SdlcError(
                "FORBIDDEN",
                f"Signing off '{stage['name']}' requires one of its assigned reviewers ({who}).",
            )

        current = await self._dynamo.get_phase_state(project_id, phase)
        if not current or current["status"] != "PENDING_REVIEW":
            raise SdlcError(
                "GATE_CONFLICT",
                f"Stage '{stage['name']}' is {current['status'] if current else 'NOT_STARTED'}, not PENDING_REVIEW",
            )

        if decision == "APPROVE":
            # Record THIS user's sign-off of every artifact in the stage (one click
            # covers the artifacts in their remit). The gate completes only when every
            # artifact is signed by every required reviewer — a SUPER_ADMIN / managing
            # PM override still force-completes as the escape hatch.
            signable = await self._stage_signable_ids(project_id, phase)
            if not override:
                for aid in signable:
                    await self._db.record_artifact_signoff(
                        id=str(uuid.uuid4()), project_id=project_id, phase=phase,
                        artefact_id=aid, user_id=user.id, user_email=user.email.lower(),
                    )
            state = await self._signoff_state(project_id, phase, required_users, signable)
            complete = override or state["complete"]
            if not complete:
                self._audit.record(
                    project_id=project_id, phase=phase, agent_role="GateController",
                    event="gate.signoff", human_reviewer=user.email,
                    detail={"stage": stage["key"], "signedUsers": state["signedUsers"],
                            "remainingUsers": state["remainingUsers"]},
                )
                return {"projectId": project_id, "phase": phase, "status": "PENDING_REVIEW",
                        "complete": False, **state}
            return await self._finalize_gate(project_id, phase, stage, wf, user, override, state)

        # AMEND
        if not comments or not comments.strip():
            raise SdlcError("VALIDATION_FAILED", "AMEND requires comments describing the required changes")
        # Guardrail: amend comments are injected into the regeneration
        # prompt, so they are screened like any other model input.
        enforce_input(comments, channel="gate_review")
        await self._dynamo.transition_phase_state(
            project_id=project_id, phase=phase,
            expected="PENDING_REVIEW", next_status="AMEND_REQUESTED",
            reviewed_by=user.email, comments=comments,
        )
        # Changes requested → the content will be regenerated, so any partial
        # sign-offs are no longer valid; every reviewer must sign the new version.
        await self._db.clear_phase_signoffs(project_id, phase)
        self._audit.record(
            project_id=project_id, phase=phase, agent_role="GateController",
            event="gate.amend_requested", human_reviewer=user.email, artefact_body=comments,
            detail={"role": user.role, "stage": stage["key"], "superAdminOverride": override},
        )
        # no silent regeneration. Seed a Plan Review draft pre-filled with the
        # reviewer's requested changes; a writer reviews the plan and explicitly
        # triggers the re-generation. The stage stays AMEND_REQUESTED until then.
        try:
            existing = await self._db.get_stage_plan(project_id, phase)
            base = (existing["prompt_overlay"].strip() + "\n\n") if (existing and existing["prompt_overlay"].strip()) else ""
            await self._db.upsert_stage_plan(
                project_id=project_id, phase=phase,
                prompt_overlay=f"{base}Reviewer's requested changes ({user.email}):\n{comments.strip()}",
                referenced_artifact_ids=(existing["referenced_artifact_ids"] if existing else []) or [],
                attachment_ids=(existing["attachment_ids"] if existing else []) or [],
                formwork_ids=(existing["formwork_ids"] if existing else []) or [],
                origin="amend", updated_by=user.id,
            )
        except Exception as err:  # noqa: BLE001 — draft seeding is best-effort
            log.error("amend plan-draft seed failed: %s", err)
        return {"projectId": project_id, "phase": phase, "status": "AMEND_REQUESTED", "nextPhase": None, "planReview": True}

    async def sign_off_artifact(
        self, *, project_id: str, phase: int, artefact_id: str, user: UserPublic,
    ) -> dict[str, Any]:
        """Record one reviewer's sign-off of ONE artifact (artifact-level review).
        Completes the stage when every artifact is signed by every required user."""
        wf = await self._workflow.view(project_id)
        stage = next((s for s in wf["stages"] if s["seq"] == phase), None)
        if not stage:
            raise SdlcError("NOT_FOUND", f"No stage at position {phase}")
        required_users = await self._required_reviewer_users(project_id, stage)
        project = await self._db.get_project(project_id)
        override = user.role == "SUPER_ADMIN" or (
            user.role == "PROJECT_MANAGER" and project and project["created_by"] == user.id
        )
        if not override and user.email.lower() not in required_users:
            who = ", ".join(sorted(required_users)) or "an assigned reviewer"
            raise SdlcError("FORBIDDEN", f"Only an assigned reviewer ({who}) can sign off this artifact.")
        current = await self._dynamo.get_phase_state(project_id, phase)
        if not current or current["status"] != "PENDING_REVIEW":
            raise SdlcError("GATE_CONFLICT", f"Stage {phase} is not awaiting review")
        signable = await self._stage_signable_ids(project_id, phase)
        if artefact_id not in signable:
            raise SdlcError("NOT_FOUND", "That artifact is not part of this stage's current outputs")
        await self._db.record_artifact_signoff(
            id=str(uuid.uuid4()), project_id=project_id, phase=phase,
            artefact_id=artefact_id, user_id=user.id, user_email=user.email.lower(),
        )
        state = await self._signoff_state(project_id, phase, required_users, signable)
        if state["complete"]:
            return await self._finalize_gate(project_id, phase, stage, wf, user, False, state)
        self._audit.record(
            project_id=project_id, phase=phase, agent_role="GateController",
            event="gate.signoff", human_reviewer=user.email,
            detail={"stage": stage["key"], "artefactId": artefact_id, "signedUsers": state["signedUsers"]},
        )
        return {"projectId": project_id, "phase": phase, "status": "PENDING_REVIEW", "complete": False, **state}

    async def _required_reviewer_users(self, project_id: str, stage: dict) -> set[str]:
        """The reviewer USER emails that must sign this stage (lower-cased). PM's
        explicit reviewerUsers wins; otherwise default to project members whose role
        is one of the stage's reviewer roles."""
        explicit = [u.lower() for u in (stage.get("reviewerUsers") or []) if u]
        if explicit:
            return set(explicit)
        roles = set(stage.get("reviewerRoles") or [stage["reviewerRole"]])
        members = await self._db.list_members(project_id)
        return {m["email"].lower() for m in members if m["role"] in roles}

    async def _stage_signable_ids(self, project_id: str, phase: int) -> list[str]:
        """The artifact ids in the stage that must be signed. Falls back to a single
        synthetic stage id when the stage produced no artifacts."""
        arts = [a for a in await self._db.list_artefacts(project_id) if a["phase"] == phase]
        return [a["id"] for a in arts] or [f"stage:{phase}"]

    async def _signoff_state(
        self, project_id: str, phase: int, required_users: set[str], signable: list[str],
    ) -> dict[str, Any]:
        """Per-artifact sign-off progress and whether the stage is complete (every
        artifact signed by every required user)."""
        rows = await self._db.list_phase_signoffs(project_id, phase)
        by_art: dict[str, set[str]] = {}
        for r in rows:
            by_art.setdefault(r["artefact_id"], set()).add((r["user_email"] or "").lower())
        signed_users = sorted({u for s in by_art.values() for u in s})
        if required_users:
            complete = all(required_users <= by_art.get(aid, set()) for aid in signable)
            remaining = sorted({u for aid in signable for u in (required_users - by_art.get(aid, set()))})
        else:
            complete = bool(signed_users)  # no explicit reviewers → a single sign-off completes
            remaining = []
        return {
            "requiredUsers": sorted(required_users),
            "signedUsers": signed_users,
            "remainingUsers": remaining,
            "artefacts": [{"artefactId": aid, "signedBy": sorted(by_art.get(aid, set()))} for aid in signable],
            "complete": complete,
        }

    async def signoff_status(self, project_id: str, phase: int) -> dict[str, Any]:
        """Sign-off progress for a stage — used by the gate UI."""
        wf = await self._workflow.view(project_id)
        stage = next((s for s in wf["stages"] if s["seq"] == phase), None)
        if not stage:
            raise SdlcError("NOT_FOUND", f"No stage at position {phase}")
        required_users = await self._required_reviewer_users(project_id, stage)
        signable = await self._stage_signable_ids(project_id, phase)
        return {"phase": phase, **await self._signoff_state(project_id, phase, required_users, signable)}

    async def _finalize_gate(
        self, project_id: str, phase: int, stage: dict, wf: dict, user: UserPublic,
        override: bool, state: dict,
    ) -> dict[str, Any]:
        """All required reviewers have signed (or an admin override) — publish the
        queued external writes, transition the gate to APPROVED and advance."""
        if self._publisher is not None:
            published = await self._publisher.publish(
                project_id=project_id, phase=phase, approver_email=user.email,
            )
        else:
            published = {"published": 0}
        await self._dynamo.transition_phase_state(
            project_id=project_id, phase=phase,
            expected="PENDING_REVIEW", next_status="APPROVED", reviewed_by=user.email,
        )
        await self._dynamo.clear_phase_stale(project_id=project_id, phase=phase)
        next_phase = await self._advance_if_level_done(project_id, phase, wf)
        self._audit.record(
            project_id=project_id, phase=phase, agent_role="GateController",
            event="gate.approved_override" if override else "gate.approved",
            human_reviewer=user.email,
            detail={"role": user.role, "stage": stage["key"], "nextPhase": next_phase,
                    "superAdminOverride": override, "published": published.get("published", 0),
                    "signedBy": state.get("signedUsers")},
        )
        return {"projectId": project_id, "phase": phase, "status": "APPROVED", "complete": True,
                "nextPhase": next_phase, "published": published.get("published", 0),
                "requiredUsers": state.get("requiredUsers"), "signedUsers": state.get("signedUsers"),
                "artefacts": state.get("artefacts")}

    async def _advance_if_level_done(self, project_id: str, phase: int, wf: dict) -> int | None:
        """Parallel-group semantics: advance only when every stage gate
        in the current level is APPROVED; then move to the next level's first
        seq, or complete the project after the last level."""
        levels: list[list[int]] = wf["levels"]
        level_idx = next((i for i, seqs in enumerate(levels) if phase in seqs), None)
        if level_idx is None:
            return None
        states = {s["SK"]: s for s in await self._dynamo.list_phase_states(project_id)}
        level_done = all(
            (states.get(f"PHASE#{seq}") or {}).get("status") == "APPROVED"
            for seq in levels[level_idx]
        )
        if not level_done:
            return None  # siblings still open — project stays at this level
        if level_idx + 1 < len(levels):
            next_seq = levels[level_idx + 1][0]
            await self._db.set_project_phase(project_id, next_seq, "ACTIVE")
            # gate approval is pull-based (nothing runs until a human
            # triggers it), so give the next stages' teams push-based awareness —
            # one durable notification per newly-ready stage, targeted at its team.
            await self._notify_stages_ready(
                project_id, [s for s in wf["stages"] if s["seq"] in levels[level_idx + 1]]
            )
            return next_seq
        max_seq = max(s["seq"] for s in wf["stages"])
        await self._db.set_project_phase(project_id, max_seq, "COMPLETED")
        await self._notify_project_completed(project_id)
        return None

    async def _notify_stages_ready(self, project_id: str, stages: list[dict]) -> None:
        """Best-effort (a notification failure must never roll back an approval)."""
        for stage in stages:
            team = stage.get("team") or []
            try:
                await self._db.insert_notification(
                    project_id=project_id, phase=stage["seq"], kind="stage_ready",
                    title=f"Stage {stage['seq']} — {stage['name']} is ready to run",
                    body=(
                        "All gates in the previous level are approved. "
                        + (f"Team ({', '.join(team)}): " if team else "")
                        + "provide input and trigger generation when ready."
                    ),
                    roles=team,
                )
                self._audit.record(
                    project_id=project_id, phase=stage["seq"], agent_role="GateController",
                    event="stage.ready", detail={"stage": stage["key"], "team": team},
                )
            except Exception as err:  # noqa: BLE001
                log.error("stage-ready notification failed for %s: %s", stage.get("key"), err)

    async def _notify_project_completed(self, project_id: str) -> None:
        try:
            await self._db.insert_notification(
                project_id=project_id, phase=None, kind="project_completed",
                title="All stages approved — project complete",
                body="Every gate in the workflow has been signed off.",
                roles=[],
            )
        except Exception as err:  # noqa: BLE001
            log.error("project-completed notification failed: %s", err)
