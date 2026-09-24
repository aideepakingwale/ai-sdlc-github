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
        project = await self._db.get_project(project_id)
        override = self._is_override(project, user)
        reviewers = await self._required_reviewer_users(project_id, stage)

        current = await self._dynamo.get_phase_state(project_id, phase)
        if not current or current["status"] != "PENDING_REVIEW":
            raise SdlcError(
                "GATE_CONFLICT",
                f"Stage '{stage['name']}' is {current['status'] if current else 'NOT_STARTED'}, not PENDING_REVIEW",
            )

        if decision == "APPROVE":
            # Normal completion happens automatically once every assigned review is
            # signed off in the matrix. This endpoint's APPROVE is the managing-PM /
            # admin OVERRIDE that force-completes the gate as the escape hatch.
            if not override:
                raise SdlcError(
                    "FORBIDDEN",
                    "Sign off your assigned items in the review matrix — the stage completes automatically "
                    "when every document is reviewed. Only a managing PM or admin can force-complete here.",
                )
            matrix = await self._review_matrix(project_id, phase, stage)
            return await self._finalize_gate(project_id, phase, stage, wf, user, True, matrix)

        # AMEND — any authorised reviewer (or override) can request changes.
        if not override and user.email.lower() not in reviewers:
            who = ", ".join(reviewers) or "an assigned reviewer"
            raise SdlcError("FORBIDDEN", f"Requesting changes on '{stage['name']}' needs a reviewer ({who}).")
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

    async def _required_reviewer_users(self, project_id: str, stage: dict) -> list[str]:
        """The reviewer USER emails available for this stage (the matrix columns),
        lower-cased. PM's explicit reviewerUsers wins; otherwise default to project
        members whose role is one of the stage's reviewer roles."""
        explicit = [u.lower() for u in (stage.get("reviewerUsers") or []) if u]
        if explicit:
            return sorted(dict.fromkeys(explicit))
        roles = set(stage.get("reviewerRoles") or [stage["reviewerRole"]])
        members = await self._db.list_members(project_id)
        return sorted({m["email"].lower() for m in members if m["role"] in roles})

    async def _stage_artifacts(self, project_id: str, phase: int) -> list[dict[str, str]]:
        return [{"id": a["id"], "type": a["type"]}
                for a in await self._db.list_artefacts(project_id) if a["phase"] == phase]

    def _is_override(self, project: dict | None, user: UserPublic) -> bool:
        return user.role == "SUPER_ADMIN" or (
            user.role == "PROJECT_MANAGER" and bool(project) and project["created_by"] == user.id
        )

    async def _review_matrix(self, project_id: str, phase: int, stage: dict) -> dict[str, Any]:
        """Assignment + sign-off matrix. Reviewers (columns) are marked against rows:
        the ENTIRE STAGE, each OUTPUT TYPE, or each generated ARTIFACT. A reviewer
        signs the scope assigned to them. The stage completes when every artifact is
        covered by an assignment and every assignment has been signed off."""
        reviewers = await self._required_reviewer_users(project_id, stage)
        artifacts = await self._stage_artifacts(project_id, phase)
        output_types = list(dict.fromkeys(stage.get("outputs") or []))

        assigns = await self._db.list_phase_assignments(project_id, phase)
        assigned_by_target: dict[str, set[str]] = {}
        for r in assigns:
            assigned_by_target.setdefault(r["target"], set()).add((r["user_email"] or "").lower())
        signs = await self._db.list_phase_signoffs(project_id, phase)
        signed_by_target: dict[str, set[str]] = {}
        for r in signs:
            signed_by_target.setdefault(r["artefact_id"], set()).add((r["user_email"] or "").lower())

        def cell(target: str) -> dict[str, list[str]]:
            return {"assigned": sorted(assigned_by_target.get(target, set())),
                    "signed": sorted(signed_by_target.get(target, set()))}

        rows: list[dict[str, Any]] = [{"kind": "stage", "target": "stage", "label": "Entire stage"}]
        rows += [{"kind": "type", "target": f"type:{t}", "label": t, "type": t} for t in output_types]
        rows += [{"kind": "artifact", "target": a["id"], "artefactId": a["id"], "type": a["type"]} for a in artifacts]
        cells = {row["target"]: cell(row["target"]) for row in rows}

        def artifact_targets(a: dict[str, str]) -> list[str]:
            return ["stage", f"type:{a['type']}", a["id"]]

        def reviewed(a: dict[str, str]) -> bool:
            # signed by someone who was assigned to any scope covering this artifact
            for t in artifact_targets(a):
                if assigned_by_target.get(t, set()) & signed_by_target.get(t, set()):
                    return True
            return False

        def covered(a: dict[str, str]) -> bool:
            return any(assigned_by_target.get(t) for t in artifact_targets(a))

        reviewed_ids = [a["id"] for a in artifacts if reviewed(a)]
        all_covered = all(covered(a) for a in artifacts)
        # Every assignment must be individually signed by its assigned reviewer.
        all_assignments_signed = all(
            u in signed_by_target.get(target, set())
            for target, users in assigned_by_target.items() for u in users
        )
        has_assignment = bool(assigns)
        complete = (
            has_assignment and all_assignments_signed
            and (all_covered if artifacts else True)
            and (len(reviewed_ids) == len(artifacts) if artifacts else True)
        )
        return {
            "phase": phase, "reviewers": reviewers, "outputTypes": output_types,
            "rows": rows, "cells": cells,
            "reviewedArtefacts": reviewed_ids,
            "signedCount": len(reviewed_ids), "totalCount": len(artifacts),
            "complete": complete,
        }

    async def signoff_status(self, project_id: str, phase: int) -> dict[str, Any]:
        """Review matrix + progress for a stage — used by the gate UI."""
        wf = await self._workflow.view(project_id)
        stage = next((s for s in wf["stages"] if s["seq"] == phase), None)
        if not stage:
            raise SdlcError("NOT_FOUND", f"No stage at position {phase}")
        return await self._review_matrix(project_id, phase, stage)

    async def assign_review(
        self, *, project_id: str, phase: int, target: str, user_email: str, assigned: bool, actor: UserPublic,
    ) -> dict[str, Any]:
        """Mark (or unmark) that `user_email` reviews `target` (stage / type:<T> /
        artifact id). Managing PM or SUPER_ADMIN only."""
        wf = await self._workflow.view(project_id)
        stage = next((s for s in wf["stages"] if s["seq"] == phase), None)
        if not stage:
            raise SdlcError("NOT_FOUND", f"No stage at position {phase}")
        project = await self._db.get_project(project_id)
        if not self._is_override(project, actor):
            raise SdlcError("FORBIDDEN", "Only the managing Project Manager or an admin can assign reviewers.")
        email = user_email.strip().lower()
        if assigned:
            await self._db.add_review_assignment(
                id=str(uuid.uuid4()), project_id=project_id, phase=phase, target=target, user_email=email)
        else:
            await self._db.remove_review_assignment(
                project_id=project_id, phase=phase, target=target, user_email=email)
        self._audit.record(project_id=project_id, phase=phase, agent_role="GateController",
                           event="review.assigned" if assigned else "review.unassigned", human_reviewer=actor.email,
                           detail={"stage": stage["key"], "target": target, "user": email})
        return await self._review_matrix(project_id, phase, stage)

    async def sign_off_target(
        self, *, project_id: str, phase: int, target: str, user: UserPublic,
    ) -> dict[str, Any]:
        """A reviewer signs off a target they are assigned to (stage / type / artifact).
        Completes the stage when every artifact is covered and every assignment signed."""
        wf = await self._workflow.view(project_id)
        stage = next((s for s in wf["stages"] if s["seq"] == phase), None)
        if not stage:
            raise SdlcError("NOT_FOUND", f"No stage at position {phase}")
        current = await self._dynamo.get_phase_state(project_id, phase)
        if not current or current["status"] != "PENDING_REVIEW":
            raise SdlcError("GATE_CONFLICT", f"Stage {phase} is not awaiting review")
        project = await self._db.get_project(project_id)
        override = self._is_override(project, user)
        email = user.email.lower()
        assigns = await self._db.list_phase_assignments(project_id, phase)
        assigned_here = any(a["target"] == target and (a["user_email"] or "").lower() == email for a in assigns)
        if not assigned_here and not override:
            raise SdlcError("FORBIDDEN", "You are not assigned to review this item.")
        await self._db.record_artifact_signoff(
            id=str(uuid.uuid4()), project_id=project_id, phase=phase,
            artefact_id=target, user_id=user.id, user_email=email)
        matrix = await self._review_matrix(project_id, phase, stage)
        if matrix["complete"]:
            return await self._finalize_gate(project_id, phase, stage, wf, user, False, matrix)
        self._audit.record(project_id=project_id, phase=phase, agent_role="GateController",
                           event="gate.signoff", human_reviewer=user.email,
                           detail={"stage": stage["key"], "target": target})
        return {"projectId": project_id, "phase": phase, "status": "PENDING_REVIEW", **matrix}

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
                    "reviewed": state.get("reviewedArtefacts")},
        )
        return {"projectId": project_id, "phase": phase, "status": "APPROVED", "complete": True,
                "nextPhase": next_phase, "published": published.get("published", 0), **state}

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
