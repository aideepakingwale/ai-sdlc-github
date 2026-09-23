"""Gate Controller: RBAC-checked human sign-off over
the project's DYNAMIC workflow. Stages in the same derived level form a
parallel group — their gates are independent, and the project advances to the
next level only when every gate in the current level is APPROVED."""

from __future__ import annotations

import logging
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
        override = await self._authz.assert_can_review_gate(
            project_id, stage.get("reviewerRoles") or [stage["reviewerRole"]], user, stage["name"]
        )

        current = await self._dynamo.get_phase_state(project_id, phase)
        if not current or current["status"] != "PENDING_REVIEW":
            raise SdlcError(
                "GATE_CONFLICT",
                f"Stage '{stage['name']}' is {current['status'] if current else 'NOT_STARTED'}, not PENDING_REVIEW",
            )

        if decision == "APPROVE":
            # Governance: the approver's sign-off is what authorises the
            # external writes. Publish the phase's queued Jira/Confluence/GitHub
            # actions FIRST — attributed to the approver — and only transition the
            # gate to APPROVED if that succeeds. A publish failure leaves the gate
            # PENDING_REVIEW so it can be retried once the integration is fixed.
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
            # Approving clears any stale flag: the reviewer has accepted this output
            # against the current upstream (whether re-generated or accepted as-is).
            await self._dynamo.clear_phase_stale(project_id=project_id, phase=phase)
            next_phase = await self._advance_if_level_done(project_id, phase, wf)
            self._audit.record(
                project_id=project_id, phase=phase, agent_role="GateController",
                event="gate.approved_override" if override else "gate.approved",
                human_reviewer=user.email,
                detail={"role": user.role, "stage": stage["key"], "nextPhase": next_phase,
                        "superAdminOverride": override, "published": published.get("published", 0)},
            )
            return {"projectId": project_id, "phase": phase, "status": "APPROVED",
                    "nextPhase": next_phase, "published": published.get("published", 0)}

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
