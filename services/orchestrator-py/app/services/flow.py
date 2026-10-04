"""Project pipeline flow + stage retrigger (D-24).

`flow()` returns a per-stage view for the visual pipeline: colour-coded state,
the reviewer role, the assigned team member (who is working the stage),
artifact counts, and per-viewer capability flags (canReview / canRetrigger).

`retrigger()` re-runs a single stage's agent (RBAC: the stage's own member,
the managing PM, or SUPER_ADMIN). It purges that stage's artifacts from the
content-store + DB, drops the phase to that stage, and regenerates — leaving the
gate PENDING_REVIEW again. Downstream approved stages are left intact; the
reviewer re-approves to move forward.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from ..agile.rules import assert_stage_mutable
from ..agents.schemas import PHASE_SCHEMAS
from ..domain.errors import SdlcError
from ..domain.models import PhaseStatus, UserPublic
from ..repos.aws import DynamoStore
from ..repos.pg import Database
from .audit import AuditService
from .authz import AuthzService
from .content_store import ContentStore

# UI colour tokens per stage state (consumed by the frontend PipelineFlow).
STATE_COLOR: dict[str, str] = {
    "NOT_STARTED": "slate",     # planned
    "IN_PROGRESS": "blue",      # in progress
    "PENDING_REVIEW": "amber",  # review
    "AMEND_REQUESTED": "orange",
    "APPROVED": "emerald",      # completed
    "ESCALATED": "red",
}

Regenerate = Callable[[str, int, UserPublic], Awaitable[None]]

log = logging.getLogger("flow")

# Downstream stages carry this flag once an upstream input is re-generated; it is
# advisory (status is untouched) and cleared when the stage is re-run.
STALE_STATUSES = ("APPROVED", "PENDING_REVIEW", "AMEND_REQUESTED", "ESCALATED")


def transitive_downstream_seqs(stages: list[dict[str, Any]], phase: int) -> list[int]:
    """Seqs of every stage that depends on `phase` directly or transitively,
    following the ``dependsOn`` DAG. Used to propagate staleness when an upstream
    stage is re-generated."""
    key_to_seq = {s["key"]: s["seq"] for s in stages}
    seq_to_key = {s["seq"]: s["key"] for s in stages}
    dependents: dict[str, list[str]] = {}
    for s in stages:
        for dep in (s.get("dependsOn") or []):
            dependents.setdefault(dep, []).append(s["key"])
    src_key = seq_to_key.get(phase)
    if src_key is None:
        return []
    seen: set[str] = set()
    queue = list(dependents.get(src_key, []))
    while queue:
        k = queue.pop()
        if k in seen:
            continue
        seen.add(k)
        queue.extend(dependents.get(k, []))
    return sorted(key_to_seq[k] for k in seen if k in key_to_seq)


class FlowService:
    def __init__(
        self, db: Database, dynamo: DynamoStore, audit: AuditService,
        authz: AuthzService, content: ContentStore, workflow: Any, regenerate: Regenerate,
    ) -> None:
        self._db = db
        self._dynamo = dynamo
        self._audit = audit
        self._authz = authz
        self._content = content
        self._workflow = workflow
        self._regenerate = regenerate

    # Iterative projects keep every sprint's stages forever; the UI polls this endpoint, so older CLOSED sprints
    # are summarised instead of sent in full (ask for one with `sprints=["S-004"]`).
    KEEP_CLOSED_SPRINTS = 2

    async def flow(self, project_id: str, viewer: UserPublic, sprints: list[str] | None = None, *, window: bool = True) -> dict[str, Any]:
        """Rendered straight from the persisted workflow config (D-30): dynamic
        stages, derived order, parallel levels, team composition and I/O."""
        project = await self._db.get_project(project_id)
        if not project:
            raise SdlcError("NOT_FOUND", "Project not found")
        wf = await self._workflow.view(project_id)
        states = {s["SK"]: s for s in await self._dynamo.list_phase_states(project_id)}
        members_by_role: dict[str, Any] = {m["role"]: m for m in await self._db.list_members(project_id)}
        artifact_rows = await self._db.list_artefacts(project_id)
        counts: dict[int, int] = {}
        for a in artifact_rows:
            counts[a["phase"]] = counts.get(a["phase"], 0) + 1

        viewer_membership = (
            None if viewer.role in ("SUPER_ADMIN", "PROJECT_MANAGER")
            else await self._authz.get_membership_role(project_id, viewer.id)
        )
        is_manager = viewer.role == "SUPER_ADMIN" or (
            viewer.role == "PROJECT_MANAGER" and project["created_by"] == viewer.id
        )

        stages = []
        for s in wf["stages"]:
            seq = s["seq"]
            st = states.get(f"PHASE#{seq}")
            status: PhaseStatus = st["status"] if st else "NOT_STARTED"
            assignee = members_by_role.get(s["reviewerRole"])
            team_members = [
                {"displayName": members_by_role[r]["display_name"], "email": members_by_role[r]["email"], "role": r}
                for r in s["team"] if r in members_by_role
            ]
            # Multi-reviewer gates + separate read/write authority (D-42).
            reviewers = s.get("reviewerRoles") or [s["reviewerRole"]]
            writers = s.get("writeRoles") or s["team"]
            # D-90: per-user ACL is authoritative when the stage defines it; the
            # role checks remain the fallback for legacy/role-defined stages.
            perms = s.get("userPerms") or []
            viewer_email = (getattr(viewer, "email", "") or "").strip().lower()
            stage_reviewer_users = {(e or "").strip().lower() for e in (s.get("reviewerUsers") or [])}
            if perms:
                write_emails = {(p.get("email") or "").strip().lower() for p in perms if p.get("write")}
                gate_emails = {(p.get("email") or "").strip().lower() for p in perms if p.get("gate")}
                may_review = viewer_email in (gate_emails | stage_reviewer_users)
                may_write = viewer_email in write_emails
            else:
                may_review = viewer_membership in reviewers or viewer_email in stage_reviewer_users
                may_write = viewer_membership in writers
            can_review = status == "PENDING_REVIEW" and (viewer.role == "SUPER_ADMIN" or may_review)
            can_retrigger = status in ("APPROVED", "PENDING_REVIEW", "AMEND_REQUESTED", "ESCALATED") and (
                is_manager or may_write
            )
            stages.append({
                "phase": seq,
                "key": s["key"],
                "name": s["name"],
                "persona": s["persona"],
                "template": s["template"],
                "reviewerRole": s["reviewerRole"],
                "reviewerRoles": reviewers,
                "readRoles": s.get("readRoles") or s["team"],
                "writeRoles": writers,
                "team": s["team"],
                "teamMembers": team_members,
                "inputs": s["inputs"],
                "outputs": s["outputs"],
                "dependsOn": s["dependsOn"],
                "level": s["level"],
                # Agile delivery (None/"project"/"full" for classic waterfall stages)
                "scope": s.get("scope", "project"),
                "agileRole": s.get("agileRole"),
                "gateMode": s.get("gateMode", "full"),
                "baseKey": s.get("baseKey", s["key"]),
                "iteration": s.get("iteration"),
                "iterationLabel": s.get("iterationLabel"),
                "iterationId": s.get("iterationId"),
                "release": s.get("release"),
                "status": status,
                "color": STATE_COLOR.get(status, "slate"),
                "artifactCount": counts.get(seq, 0),
                "reviewedBy": st.get("reviewedBy") if st else None,
                "updatedAt": st.get("updatedAt") if st else None,
                # Impact propagation: set when an upstream input was re-generated
                # after this stage had already consumed it (status is untouched).
                "stale": bool(st.get("stale")) if st else False,
                "staleReason": st.get("staleReason") if st else None,
                "staleSource": st.get("staleSource") if st else None,
                "staleSince": st.get("staleSince") if st else None,
                # Multi-reviewer sign-off progress (detail via the signoffs endpoint).
                "requiredReviewers": reviewers,
                # Entry stage (no dependencies) — a dynamic workflow can start here.
                "isEntry": not (s.get("dependsOn") or []),
                "assignee": None if not assignee else {
                    "displayName": assignee["display_name"],
                    "email": assignee["email"],
                    "role": assignee["role"],
                },
                "canReview": can_review,
                "canRetrigger": can_retrigger,
            })

        collapsed: list[dict[str, Any]] = []
        levels = wf["levels"]
        if wf.get("iterative") and window:
            stages, levels, collapsed = self._window_sprints(stages, levels, sprints or [])
        return {
            "projectId": project_id,
            "name": project["name"],
            "techStack": project.get("tech_stack"),
            "currentPhase": project["current_phase"],
            "status": project["status"],
            "isManager": is_manager,
            "viewerRole": viewer.role,
            "workflowVersion": wf["version"],
            "methodology": wf.get("methodology", "waterfall"),
            "iterative": bool(wf.get("iterative")),
            "collapsedSprints": collapsed,
            "levels": levels,
            "stages": stages,
        }

    def _window_sprints(
        self, stages: list[dict[str, Any]], levels: list[list[int]], wanted: list[str],
    ) -> tuple[list[dict[str, Any]], list[list[int]], list[dict[str, Any]]]:
        """Keep full stage detail for the foundation, the open/planned sprint, the most recent closed sprints,
        every release stage and any sprint explicitly asked for; summarise the rest."""
        by_label: dict[str, list[dict[str, Any]]] = {}
        for st in stages:
            if st.get("iterationLabel"):
                by_label.setdefault(st["iterationLabel"], []).append(st)

        def settled(group: list[dict[str, Any]]) -> bool:
            return all(g["status"] == "APPROVED" for g in group)

        closed = sorted((lb for lb, g in by_label.items() if settled(g)), key=lambda lb: by_label[lb][0].get("iteration") or 0)
        hide = set(closed[:-self.KEEP_CLOSED_SPRINTS] if self.KEEP_CLOSED_SPRINTS else closed) - set(wanted)
        if not hide:
            return stages, levels, []
        gone = {g["phase"] for lb in hide for g in by_label[lb]}
        kept = [st for st in stages if st["phase"] not in gone]
        new_levels = [[q for q in lv if q not in gone] for lv in levels]
        collapsed = [{"label": lb, "number": by_label[lb][0].get("iteration"), "status": "closed",
                      "stages": len(by_label[lb]), "approved": len(by_label[lb])} for lb in sorted(hide, key=lambda x: by_label[x][0].get("iteration") or 0)]
        return kept, [lv for lv in new_levels if lv], collapsed

    async def mark_downstream_stale(self, project_id: str, phase: int, reason: str) -> list[int]:
        """Flag every downstream stage that already consumed this stage's output as
        stale (impact propagation). Non-destructive: statuses are preserved, only a
        badge is added, so the reviewer decides whether to regenerate. Returns the
        affected stage seqs."""
        wf = await self._workflow.view(project_id)
        stages = wf["stages"]
        downstream = transitive_downstream_seqs(stages, phase)
        if not downstream:
            return []
        states = {s["SK"]: s for s in await self._dynamo.list_phase_states(project_id)}
        affected: list[int] = []
        for seq in downstream:
            st = states.get(f"PHASE#{seq}")
            status = st["status"] if st else "NOT_STARTED"
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
            log.info("flagged downstream stages %s stale (upstream %s changed)", affected, phase)
        return affected

    async def retrigger(self, project_id: str, phase: int, user: UserPublic) -> dict[str, Any]:
        stage = await self._workflow.stage_by_seq(project_id, phase)

        await self._assert_can_write(project_id, stage, user)
        await assert_stage_mutable(self._db, stage)

        current = await self._dynamo.get_phase_state(project_id, phase)
        if not current or current["status"] == "NOT_STARTED":
            raise SdlcError("GATE_CONFLICT", f"Stage {phase} has not run yet — nothing to retrigger")

        # Version, don't destroy: supersede this stage's current artifacts (kept
        # as history, bodies retained), then rewind so the pipeline regenerates —
        # the new set becomes the latest versions.
        await self._db.supersede_phase_artefacts(project_id, phase)
        await self._db.set_project_phase(project_id, phase, "ACTIVE")
        # D-56: rewind to NOT_STARTED and route through the Plan Review gate — the
        # writer reviews (and may edit) the plan, then explicitly triggers. No
        # silent auto-regeneration.
        await self._dynamo.put_phase_state(
            project_id=project_id, phase=phase, status="NOT_STARTED", reviewer_role=stage["reviewerRole"],
        )
        existing = await self._db.get_stage_plan(project_id, phase)
        await self._db.upsert_stage_plan(
            project_id=project_id, phase=phase,
            prompt_overlay=(existing["prompt_overlay"] if existing else "") or "",
            referenced_artifact_ids=(existing["referenced_artifact_ids"] if existing else []) or [],
            attachment_ids=(existing["attachment_ids"] if existing else []) or [],
            formwork_ids=(existing["formwork_ids"] if existing else []) or [],
            origin="retrigger", updated_by=user.id,
        )
        # A re-run must go through Plan Review again: the previous plan no longer counts as final.
        await self._db.set_stage_plan_sig(project_id, phase, None)
        # Impact propagation: re-running this stage will produce a new version of
        # its outputs, so any downstream stage that already consumed the old ones is
        # now potentially stale. Flag them immediately (advisory — statuses kept).
        stale = await self.mark_downstream_stale(
            project_id, phase, reason=f"Upstream stage '{stage['name']}' was re-run",
        )
        self._audit.record(
            project_id=project_id, phase=phase, agent_role="Orchestrator",
            event="stage.retriggered", human_reviewer=user.email,
            detail={"role": user.role, "planReview": True, "downstreamFlaggedStale": stale},
        )
        return {"projectId": project_id, "phase": phase, "status": "NOT_STARTED",
                "retriggered": True, "planReview": True, "downstreamFlaggedStale": stale}

    async def _assert_can_write(self, project_id: str, stage: dict[str, Any], user: UserPublic) -> None:
        # RBAC: SUPER_ADMIN, the managing PM, or the stage's own phase-role member.
        if user.role == "SUPER_ADMIN":
            pass
        elif user.role == "PROJECT_MANAGER":
            project = await self._db.get_project(project_id)
            if not project or project["created_by"] != user.id:
                raise SdlcError("FORBIDDEN", "Only the managing PROJECT_MANAGER can retrigger this project's stages")
        else:
            # Retriggering mutates the stage — it needs WRITE authority. D-90:
            # prefer the per-user ACL when the stage defines one; otherwise fall
            # back to the role-based writers (defaults to the whole team).
            perms = stage.get("userPerms") or []
            if perms:
                email = (getattr(user, "email", "") or "").strip().lower()
                write_emails = {(p.get("email") or "").strip().lower() for p in perms if p.get("write")}
                if email not in write_emails:
                    raise SdlcError(
                        "FORBIDDEN",
                        f"Retriggering the '{stage['name']}' stage requires write permission — "
                        f"your account is not granted write on this stage",
                    )
            else:
                writers = stage.get("writeRoles") or stage["team"]
                membership = await self._authz.get_membership_role(project_id, user.id)
                if membership not in writers:
                    raise SdlcError(
                        "FORBIDDEN",
                        f"Retriggering the '{stage['name']}' stage requires write permission "
                        f"({' or '.join(writers)}) — you are {membership or 'not a member'}",
                    )

    async def regenerate_parts(
        self, project_id: str, phase: int, fields: list[str], user: UserPublic,
    ) -> dict[str, Any]:
        """Regenerate SELECTED artifacts of a stage on demand — even after approval.

        Only the chosen parts are regenerated; every other part is reused from its
        persisted value. The stage drops back to IN_PROGRESS (it returns to review
        when done, so the changed output is re-approved) and downstream stages that
        consumed the old output are flagged stale. An empty selection means all."""
        stage = await self._workflow.stage_by_seq(project_id, phase)
        await self._assert_can_write(project_id, stage, user)
        await assert_stage_mutable(self._db, stage)
        schema = PHASE_SCHEMAS.get(stage["template"])
        if schema is None:
            raise SdlcError("VALIDATION_FAILED",
                            "This stage type has no separate artifacts; use Retrigger stage instead")
        all_fields = list(schema.model_fields)
        selected = list(dict.fromkeys(fields)) or all_fields
        unknown = [f for f in selected if f not in all_fields]
        if unknown:
            raise SdlcError("VALIDATION_FAILED", f"Unknown artifact(s): {', '.join(unknown)}")
        current = await self._dynamo.get_phase_state(project_id, phase)
        status = current["status"] if current else "NOT_STARTED"
        if status == "NOT_STARTED":
            raise SdlcError("GATE_CONFLICT", f"Stage {phase} has not run yet — nothing to regenerate")
        if status == "IN_PROGRESS":
            raise SdlcError("GATE_CONFLICT", f"Stage {phase} is already generating")
        if len(selected) < len(all_fields):
            have = {p["field"] for p in await self._db.list_generation_parts(project_id, phase)
                    if p["status"] == "done" and p.get("value_json")}
            missing = [f for f in all_fields if f not in selected and f not in have]
            if missing:
                raise SdlcError(
                    "GATE_CONFLICT",
                    f"Saved output for {', '.join(missing)} is missing (stage predates per-artifact "
                    f"saving). Regenerate all artifacts once to enable selective regeneration",
                )
        await self._dynamo.put_phase_state(
            project_id=project_id, phase=phase, status="IN_PROGRESS", reviewer_role=stage["reviewerRole"],
        )
        await self._db.set_project_phase(project_id, phase, "ACTIVE")
        stale = await self.mark_downstream_stale(
            project_id, phase, reason=f"Upstream stage '{stage['name']}' was partially regenerated",
        )
        self._audit.record(
            project_id=project_id, phase=phase, agent_role="Orchestrator",
            event="stage.parts_regenerated", human_reviewer=user.email,
            detail={"fields": selected, "all": len(selected) == len(all_fields),
                    "previousStatus": status, "downstreamFlaggedStale": stale},
        )
        return {"projectId": project_id, "phase": phase, "fields": selected,
                "previousStatus": status, "downstreamFlaggedStale": stale}

    async def delete_project(self, project_id: str, user: UserPublic) -> dict:
        """Permanently delete a project across EVERY store (D-55): the content-store
        subtree, the DynamoDB phase-states, and all Postgres rows. This is the
        cleanup path that was previously impossible (sessions/artefacts FKs blocked
        the DB delete and the external stores had no purge)."""
        await self._authz.assert_can_delete_project(project_id, user)
        project = await self._db.get_project(project_id)
        if not project:
            raise SdlcError("NOT_FOUND", "Project not found")

        # Postgres FIRST: it is the authoritative record and the delete is one
        # transaction (cascades to sessions/artefacts/etc.). Only once that commits
        # do we purge the external stores — so a DB failure can never orphan the
        # content-store or DynamoDB (nothing external is touched unless the project
        # really was removed). A best-effort external purge that fails afterwards
        # just leaves re-cleanable files, not a half-deleted project.
        deleted = await self._db.delete_project(project_id)
        files_removed = 0
        try:
            files_removed = await self._content.delete_prefix(f"content-store/{project_id}")
        except Exception as err:  # noqa: BLE001
            log.error("content-store purge failed for %s: %s", project_id, err)
        try:
            await self._dynamo.delete_phase_states(project_id)
        except Exception as err:  # noqa: BLE001
            log.error("dynamo phase-state purge failed for %s: %s", project_id, err)

        self._audit.record(
            project_id=project_id, agent_role="Orchestrator", event="project.deleted",
            human_reviewer=user.email,
            detail={"name": project["name"], "filesRemoved": files_removed, "role": user.role},
        )
        return {"projectId": project_id, "deleted": deleted, "filesRemoved": files_removed}
