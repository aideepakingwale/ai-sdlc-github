"""Projects, membership, artifacts, audit, gates, users, KB, skills, webhooks — REST layer."""

from __future__ import annotations

import hashlib
import hmac as hmac_mod

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from ..domain.errors import SdlcError
from ..services import guardrails as guardrails_svc
from ..services import prompt_library
from ..services.chat import sse_stream
from ..domain.models import (
    AddMemberRequest,
    ArtefactUpdate,
    CreateProjectRequest,
    DiagramRepairRequest,
    FeedbackRequest,
    GateReviewRequest,
    ProjectIntegrations,
    StagePlanUpdate,
    UserPublic,
    can_manage_projects,
)
from ..services import content_validators as cv
from .deps import Container, current_user, get_container

router = APIRouter()

# ------------------------------------------------------------------ model catalog
# The catalog builder lives in the services layer (plan_model) so plan derivation
# and this route share one definition; re-exported for tests that import it here.
from ..services.plan_model import build_model_catalog  # noqa: E402

_models_cache: dict[str, object] = {"at": 0.0, "data": None}


@router.get("/api/models")
async def list_models(
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    """The selectable model catalog for the Plan Review picker — one entry
    per configured provider with its model, tier hint, vision support and health.
    Platform-level (any authenticated user); cached ~30s so a plan render never
    hammers the gateway. Deterministic and zero-token."""
    import time

    now = time.monotonic()
    cached = _models_cache.get("data")
    if cached is not None and now - float(_models_cache["at"]) < 30:  # type: ignore[arg-type]
        return cached  # type: ignore[return-value]
    data = build_model_catalog(await container.llm.providers())
    _models_cache.update(at=now, data=data)
    return data


@router.get("/api/meta/tech-catalog")
async def tech_catalog(user: UserPublic = Depends(current_user)) -> dict:
    """Configurable technology catalog for the New Project form — programming
    language → version → framework(s). Platform-level (any authenticated user);
    sourced from TECH_CATALOG_PATH or the built-in default. Zero-token."""
    from ..services.tech_catalog import get_tech_catalog
    return get_tech_catalog()


def _project_row(p) -> dict:  # noqa: ANN001
    return {
        "id": p["id"], "name": p["name"], "status": p["status"],
        "currentPhase": p["current_phase"], "createdAt": p["created_at"].isoformat(),
        "techStack": p.get("tech_stack") or "Node.js + TypeScript",
        "integrations": { # per-project GitHub/Atlassian targets
            "githubRepo": p.get("github_repo"),
            "atlassianSiteUrl": p.get("atlassian_site_url"),
            "jiraProjectKey": p.get("jira_project_key"),
            "confluenceSpaceKey": p.get("confluence_space_key"),
        },
    }


# ------------------------------------------------------------------ projects
@router.get("/api/projects")
async def list_projects(
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container)
) -> dict:
    if user.role == "SUPER_ADMIN":
        rows = await container.db.list_projects_all()
    elif user.role == "PROJECT_MANAGER":
        rows = await container.db.list_projects_by_creator(user.id)
    else:
        rows = await container.db.list_projects_by_member(user.id)
    return {"projects": [_project_row(p) for p in rows]}


@router.post("/api/projects", status_code=201)
async def create_project(
    body: CreateProjectRequest,
    user: UserPublic = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    container.authz.assert_can_create_project(user)
    # Validate an optional create-time workflow BEFORE creating anything, so an
    # invalid plan never leaves a half-created project.
    if body.workflow is not None:
        from ..services.workflow import WorkflowConfig, validate_workflow
        try:
            wf_cfg = WorkflowConfig.model_validate(body.workflow)
        except Exception as err:  # noqa: BLE001 — surface as a validation error
            raise SdlcError("VALIDATION_FAILED", f"Invalid workflow: {err}") from err
        wf_errors = validate_workflow(wf_cfg)
        if wf_errors:
            raise SdlcError("VALIDATION_FAILED", " | ".join(wf_errors[:6]), {"errors": wf_errors})
    # Compose the tech_stack string from the structured selection (language →
    # version → frameworks); fall back to the legacy single-string value.
    from ..services.tech_catalog import compose_stack
    tech_stack = compose_stack(body.language, body.languageVersion, body.frameworks, fallback=body.techStack)
    project = await container.db.create_project(
        name=body.name, created_by=user.id, tech_stack=tech_stack,
        integrations=body.integrations.model_dump(),
    )
    if body.workflow is not None:
        await container.workflow.save(project["id"], body.workflow, user)
    container.audit.record(
        project_id=project["id"], phase=1, agent_role="Orchestrator",
        event="project.created", human_reviewer=user.email,
        detail={"name": body.name, "via": "api", "techStack": tech_stack,
                "integrations": body.integrations.model_dump(exclude_none=True)},
    )
    return {"project": _project_row(project)}


@router.put("/api/projects/{project_id}/integrations")
async def update_integrations(
    project_id: str, body: ProjectIntegrations,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    """Edit a project's GitHub repo + Atlassian (Jira + Confluence) targets.
    Restricted to the managing PM (creator) / SUPER_ADMIN."""
    await container.authz.assert_project_access(project_id, user)
    project = await container.db.get_project(project_id)
    if not project:
        raise SdlcError("NOT_FOUND", "Project not found")
    is_manager = user.role == "SUPER_ADMIN" or (
        user.role == "PROJECT_MANAGER" and project["created_by"] == user.id
    )
    if not is_manager:
        raise SdlcError("FORBIDDEN", "only the managing PM or a super-admin can change integration targets")
    await container.db.update_project_integrations(project_id, body.model_dump())
    container.audit.record(
        project_id=project_id, phase=1, agent_role="Orchestrator", event="project.integrations_updated",
        human_reviewer=user.email, detail=body.model_dump(exclude_none=True),
    )
    return {"ok": True, "integrations": body.model_dump()}


@router.get("/api/projects/{project_id}")
async def project_detail(
    project_id: str,
    user: UserPublic = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    await container.authz.assert_project_access(project_id, user)
    project = await container.db.get_project(project_id)
    if not project:
        raise SdlcError("NOT_FOUND", "Project not found")
    session = await container.db.get_session(project_id)
    messages = await container.db.list_chat(session["id"]) if session else []
    membership = await container.authz.get_membership_role(project_id, user.id)
    states = await container.gates.list_states(project_id, user)
    return {
        "project": _project_row(project),
        "codebaseFiles": await container.db.count_codebase_files(project_id),
        "sessionId": session["id"] if session else None,
        "contextWindow": session["context_window"] if session else [],
        "phaseStates": [s.model_dump() for s in states],
        "me": {
            "membershipRole": membership,
            "canManageTeam": user.role == "SUPER_ADMIN"
            or (user.role == "PROJECT_MANAGER" and project["created_by"] == user.id),
        },
        "messages": [
            {"id": m["id"], "role": m["role"], "content": m["content"],
             "phase": m["phase"], "createdAt": m["created_at"].isoformat()}
            for m in messages
        ],
    }


@router.delete("/api/projects/{project_id}")
async def delete_project(
    project_id: str,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    """Permanently delete a project and all its data across every store.
    Restricted to the managing PM (creator) or a SUPER_ADMIN."""
    return await container.flow.delete_project(project_id, user)


# ------------------------------------------------------------------ members
@router.get("/api/projects/{project_id}/members")
async def list_members(
    project_id: str, user: UserPublic = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    await container.authz.assert_project_access(project_id, user)
    return {"members": await container.authz.list_members(project_id)}


@router.post("/api/projects/{project_id}/members", status_code=201)
async def add_member(
    project_id: str, body: AddMemberRequest,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    member = await container.authz.add_member(project_id, user, body.email, body.role)
    container.audit.record(
        project_id=project_id, agent_role="Orchestrator", event="team.member_added",
        human_reviewer=user.email, detail={"member": member["email"], "role": member["role"]},
    )
    return {"member": member}


@router.delete("/api/projects/{project_id}/members/{user_id}")
async def remove_member(
    project_id: str, user_id: str,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    await container.authz.remove_member(project_id, user, user_id)
    container.audit.record(
        project_id=project_id, agent_role="Orchestrator", event="team.member_removed",
        human_reviewer=user.email, detail={"removedUserId": user_id},
    )
    return {"ok": True}


@router.get("/api/users")
async def list_users(
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container)
) -> dict:
    if not can_manage_projects(user.role):
        raise SdlcError("FORBIDDEN", "Only project managers and super admins can list users")
    rows = await container.db.list_users()
    return {"users": [
        {"id": r["id"], "email": r["email"], "displayName": r["display_name"], "role": r["role"]}
        for r in rows
    ]}


# ------------------------------------------------------------------ workflow config
@router.get("/api/projects/{project_id}/workflow")
async def get_workflow(
    project_id: str,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    await container.authz.assert_project_access(project_id, user)
    return await container.workflow.view(project_id)


@router.post("/api/projects/{project_id}/workflow/validate")
async def validate_workflow_route(
    project_id: str, body: dict,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    """Live designer feedback: errors + derived order/levels when valid."""
    from ..services.workflow import WorkflowConfig, derive as wf_derive, validate_workflow

    await container.authz.assert_project_access(project_id, user)
    try:
        config = WorkflowConfig.model_validate(body)
    except Exception as err:  # pydantic shape errors reported as validation output
        return {"valid": False, "errors": [str(err)[:400]]}
    errors = validate_workflow(config)
    if errors:
        return {"valid": False, "errors": errors}
    return {"valid": True, "errors": [], **wf_derive(config)}


@router.put("/api/projects/{project_id}/workflow")
async def save_workflow(
    project_id: str, body: dict,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    """Amend the project's SDLC configuration (PM of the project / SUPER_ADMIN)."""
    project = await container.db.get_project(project_id)
    if not project:
        raise SdlcError("NOT_FOUND", "Project not found")
    if user.role == "SUPER_ADMIN":
        pass
    elif user.role == "PROJECT_MANAGER" and project["created_by"] == user.id:
        pass
    else:
        raise SdlcError("FORBIDDEN", "Only the managing PROJECT_MANAGER (or SUPER_ADMIN) can change the workflow")
    return await container.workflow.save(project_id, body, user)


# ------------------------------------------------------------------ pipeline flow + retrigger
@router.get("/api/projects/{project_id}/flow")
async def project_flow(
    project_id: str, user: UserPublic = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    await container.authz.assert_project_access(project_id, user)
    return await container.flow.flow(project_id, user)


@router.get("/api/projects/{project_id}/phases/{phase_id}")
async def phase_detail(
    project_id: str, phase_id: int,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    """Phase-scoped bundle: a single stage's tasks + artifacts + skills,
    available to any user authorised on the project (managing PM, phase-role
    member, or SUPER_ADMIN). This is the 'that particular phase' access surface."""
    if not 1 <= phase_id <= 12:
        raise SdlcError("VALIDATION_FAILED", "phaseId must be 1-12")
    await container.authz.assert_project_access(project_id, user)

    flow = await container.flow.flow(project_id, user)
    stage = next((s for s in flow["stages"] if s["phase"] == phase_id), None)
    if not stage:
        raise SdlcError("NOT_FOUND", "Phase not found")

    artefacts = await container.db.list_phase_artefacts(project_id, phase_id)
    tasks = await container.db.list_phase_audit(project_id, phase_id)
    skills = await container.skills.list_for(project_id, flow["currentPhase"], user, phase_id)
    return {
        "phase": phase_id,
        "stage": stage,
        "artefacts": [
            {"id": a["id"], "phase": a["phase"], "type": a["type"], "title": a["title"],
             "url": a["url"], "createdAt": a["created_at"].isoformat()}
            for a in artefacts
        ],
        "tasks": [
            {"id": t["id"], "event": t["event"], "agentRole": t["agent_role"],
             "humanReviewer": t["human_reviewer"], "provider": t["provider"], "model": t["model"],
             "timestamp": t["timestamp"].isoformat()}
            for t in tasks
        ],
        "skills": skills,
    }


@router.get("/api/projects/{project_id}/plan-preview")
async def plan_preview(
    project_id: str, message: str = "",
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    """Run visualizer: the plan, execution path, model tier, expected MCP
    tools and stage skills for what the NEXT chat turn would run — no execution."""
    return await container.chat.plan_preview(project_id=project_id, user=user, message=message)


@router.post("/api/projects/{project_id}/phase/{phase_id}/retrigger")
async def retrigger_stage(
    project_id: str, phase_id: int,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    if not 1 <= phase_id <= 12:
        raise SdlcError("VALIDATION_FAILED", "phaseId must be 1-12")
    await container.authz.assert_project_access(project_id, user)
    return await container.flow.retrigger(project_id, phase_id, user)


# ------------------------------------------------------------------ Plan Review & Edit gate
@router.get("/api/projects/{project_id}/phase/{phase_id}/plan")
async def get_stage_plan(
    project_id: str, phase_id: int,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    """Full pre-generation plan for a stage: agent, skills, expected tools, model
    tier, context inventory and the actual system-generated prompt."""
    if not 1 <= phase_id <= 12:
        raise SdlcError("VALIDATION_FAILED", "phaseId must be 1-12")
    return await container.chat.build_plan(project_id=project_id, phase=phase_id, user=user)


@router.put("/api/projects/{project_id}/phase/{phase_id}/plan")
async def update_stage_plan(
    project_id: str, phase_id: int, body: StagePlanUpdate,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    """Save the writer's overlay edits ('Update the plan') and re-render the preview."""
    if not 1 <= phase_id <= 12:
        raise SdlcError("VALIDATION_FAILED", "phaseId must be 1-12")
    return await container.chat.save_plan(project_id=project_id, phase=phase_id, user=user, overlay=body.model_dump())


@router.post("/api/projects/{project_id}/phase/{phase_id}/plan/trigger")
async def trigger_stage_plan(
    project_id: str, phase_id: int,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> StreamingResponse:
    """Run the stage using its reviewed plan (SSE). Nothing generates until this is
    invoked by a writer; the result goes straight to gate review."""
    if not 1 <= phase_id <= 12:
        raise SdlcError("VALIDATION_FAILED", "phaseId must be 1-12")

    async def handler(emit):  # noqa: ANN001
        await container.chat.trigger_stage(project_id=project_id, phase=phase_id, user=user, emit=emit)

    return StreamingResponse(
        sse_stream(handler), media_type="text/event-stream",
        headers={"cache-control": "no-cache, no-transform", "x-accel-buffering": "no"},
    )


# ------------------------------------------------------------------ gates
@router.get("/api/gates/{project_id}")
async def gate_states(
    project_id: str, user: UserPublic = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    states = await container.gates.list_states(project_id, user)
    return {"states": [s.model_dump() for s in states]}


@router.post("/api/gates/{project_id}/phase/{phase_id}/review")
async def review_gate(
    project_id: str, phase_id: int, body: GateReviewRequest,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    if not 1 <= phase_id <= 12:
        raise SdlcError("VALIDATION_FAILED", "phaseId must be 1-12")
    return await container.gates.review(
        project_id=project_id, phase=phase_id,
        decision=body.decision, comments=body.comments, user=user,
    )


# ------------------------------------------------------------------ notifications
@router.get("/api/projects/{project_id}/notifications")
async def list_notifications(
    project_id: str, user: UserPublic = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Stage-ready / project-completed notifications, role-filtered: a targeted
    notification is shown to the stage's team roles plus PM/SUPER_ADMIN overseers."""
    await container.authz.assert_project_access(project_id, user)
    membership = None
    if user.role not in ("SUPER_ADMIN", "PROJECT_MANAGER"):
        membership = await container.authz.get_membership_role(project_id, user.id)
    rows = await container.db.list_notifications(project_id)
    out = []
    for n in rows:
        roles = n["roles"] or []
        if roles and user.role not in ("SUPER_ADMIN", "PROJECT_MANAGER") and membership not in roles:
            continue
        out.append({
            "id": n["id"], "projectId": n["project_id"], "phase": n["phase"], "kind": n["kind"],
            "title": n["title"], "body": n["body"], "roles": roles,
            "read": user.id in (n["read_by"] or []),
            "createdAt": n["created_at"].isoformat(),
        })
    return {"notifications": out}


@router.post("/api/projects/{project_id}/notifications/{notification_id}/read")
async def read_notification(
    project_id: str, notification_id: str,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    await container.authz.assert_project_access(project_id, user)
    await container.db.mark_notification_read(notification_id, user.id)
    return {"ok": True}


# ------------------------------------------------------------------ generation feedback
def _feedback_public(r) -> dict:  # noqa: ANN001
    return {
        "id": r["id"], "phase": r["phase"], "artefactId": r["artefact_id"],
        "source": r["source"], "rating": r["rating"], "category": r["category"],
        "severity": r["severity"], "comment": r["comment"], "status": r["status"],
        "createdBy": r["created_by"], "createdAt": r["created_at"].isoformat(),
        "resolvedBy": r["resolved_by"],
        "resolvedAt": r["resolved_at"].isoformat() if r["resolved_at"] else None,
    }


@router.get("/api/projects/{project_id}/phase/{phase_id}/feedback")
async def list_feedback(
    project_id: str, phase_id: int,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    """Quality signals for a stage: the validation agent's verdict (source=validation)
    plus human-reported issues (source=human) — the single quality view a reviewer
    sees before sign-off."""
    await container.authz.assert_project_access(project_id, user)
    if not 1 <= phase_id <= 12:
        raise SdlcError("VALIDATION_FAILED", "phaseId must be 1-12")
    rows = await container.db.list_feedback(project_id, phase_id)
    return {"feedback": [_feedback_public(r) for r in rows]}


@router.post("/api/projects/{project_id}/phase/{phase_id}/feedback", status_code=201)
async def report_feedback(
    project_id: str, phase_id: int, body: FeedbackRequest,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    """Any project member can report/mark a quality issue on a generation."""
    await container.authz.assert_project_access(project_id, user)
    if not 1 <= phase_id <= 12:
        raise SdlcError("VALIDATION_FAILED", "phaseId must be 1-12")
    feedback_id = await container.db.insert_feedback(
        project_id=project_id, phase=phase_id, source="human", category=body.category,
        severity=body.severity, comment=body.comment, rating=body.rating,
        artefact_id=body.artefactId, created_by=user.id,
    )
    container.audit.record(
        project_id=project_id, phase=phase_id, agent_role="Human", event="feedback.reported",
        human_reviewer=user.email,
        detail={"category": body.category, "severity": body.severity, "artefactId": body.artefactId},
    )
    return {"id": feedback_id}


@router.post("/api/projects/{project_id}/feedback/{feedback_id}/resolve")
async def resolve_feedback(
    project_id: str, feedback_id: str,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    """Mark a reported issue resolved. Restricted to users who can write a stage in
    this project (reviewers/writers/PM), not every viewer."""
    await container.authz.assert_project_access(project_id, user)
    row = await container.db.get_feedback(feedback_id)
    if not row or row["project_id"] != project_id:
        raise SdlcError("NOT_FOUND", "feedback not found")
    if not await container.chat.can_write_stage(project_id, int(row["phase"]), user):
        raise SdlcError("FORBIDDEN", "you do not have permission to resolve feedback on this stage")
    updated = await container.db.resolve_feedback(feedback_id, user.id)
    container.audit.record(
        project_id=project_id, phase=int(row["phase"]), agent_role="Human",
        event="feedback.resolved", human_reviewer=user.email, detail={"feedbackId": feedback_id},
    )
    return {"ok": True, "feedback": _feedback_public(updated)}


# ------------------------------------------------------------------ artifacts & audit
@router.get("/api/projects/{project_id}/artefacts")
async def list_artefacts(
    project_id: str, user: UserPublic = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    await container.authz.assert_project_access(project_id, user)
    rows = await container.db.list_artefacts(project_id)
    return {"artefacts": [
        {"id": a["id"], "projectId": a["project_id"], "phase": a["phase"], "type": a["type"],
         "title": a["title"], "url": a["url"], "version": a["version"],
         "createdAt": a["created_at"].isoformat()}
        for a in rows
    ]}


@router.get("/api/projects/{project_id}/artefacts/{artefact_id}")
async def artefact_detail(
    project_id: str, artefact_id: str,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    await container.authz.assert_project_access(project_id, user)
    row = await container.db.get_artefact(artefact_id)
    if not row or row["project_id"] != project_id:
        raise SdlcError("NOT_FOUND", "Artefact not found")
    # Prefer the content-store tier; fall back to the DB column for
    # rows written before the tier existed.
    content = row["content"]
    if row["storage_key"]:
        stored = await container.content.get(row["storage_key"])
        if stored is not None:
            content = stored
    data = dict(row)
    data["content"] = content
    # Whether THIS user may edit the artifact in place: stage write
    # permission for the artifact's phase. The PUT endpoint re-checks server-side.
    can_edit = await container.chat.can_write_stage(project_id, int(row["phase"]), user)
    return {
        "artefact": {
            "id": data["id"], "projectId": data["project_id"], "phase": data["phase"],
            "type": data["type"], "title": data["title"], "content": content, "url": data["url"],
            "version": data["version"], "storageMode": data["storage_mode"],
            "storageKey": data["storage_key"], "createdAt": row["created_at"].isoformat(),
            "canEdit": can_edit,
        }
    }


@router.get("/api/projects/{project_id}/artefacts/{artefact_id}/versions")
async def artefact_versions(
    project_id: str, artefact_id: str,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    """Version history of an artifact's lineage, newest first. Amend/retrigger
    supersede prior generations instead of deleting them; each entry is an
    artefact id whose content loads via the artefact detail endpoint."""
    await container.authz.assert_project_access(project_id, user)
    row = await container.db.get_artefact(artefact_id)
    if not row or row["project_id"] != project_id:
        raise SdlcError("NOT_FOUND", "Artefact not found")
    lineage = row["lineage_id"] or row["id"]
    versions = await container.db.list_artefact_versions(project_id, lineage)
    return {"versions": [
        {"id": v["id"], "version": v["version"], "isLatest": v["is_latest"],
         "title": v["title"], "type": v["type"], "createdAt": v["created_at"].isoformat()}
        for v in versions
    ]}


async def _save_artefact_edit(
    container: Container, row, project_id: str, new_content: str, user: UserPublic, *, via: str,
) -> dict:
    """Shared persistence for an authorised artefact edit — used by both the
    JSON PUT and the file Replace. Masks secrets/PII, structurally
    validates draw.io so a broken diagram can't be saved, writes the
    content-store tier + DB preview with a version bump, and audits. The caller
    has already checked project access + stage write permission."""
    new_content, masked = guardrails_svc.sanitise_output(new_content)

    key_ext = (row["storage_key"] or "").rsplit(".", 1)
    is_drawio = (row["type"] == "DRAWIO" or (len(key_ext) == 2 and key_ext[1] == "drawio")
                 or new_content.lstrip().startswith("<mxfile"))
    if is_drawio:
        from ..services.drawio import validate_drawio
        verdict = validate_drawio(new_content)
        if not verdict["ok"]:
            raise SdlcError("VALIDATION_FAILED", "draw.io diagram is not valid: " + "; ".join(verdict["errors"][:5]))

    db_content = new_content if not row["storage_key"] else new_content[:2000]
    if row["storage_key"]:
        await container.content.put(row["storage_key"], new_content)
    version = await container.db.update_artefact_content(row["id"], db_content)
    if masked:
        container.audit.record(
            project_id=project_id, phase=int(row["phase"]), agent_role="OutputGuardrail",
            event="guardrail.output_masked", detail={"rules": masked, "artefactId": row["id"]},
        )
    container.audit.record(
        project_id=project_id, phase=int(row["phase"]), agent_role="Orchestrator",
        event="artefact.updated", human_reviewer=user.email,
        detail={"artefactId": row["id"], "type": row["type"], "version": version,
                "chars": len(new_content), "via": via},
    )
    return {"ok": True, "version": version, "masked": masked}


async def _writable_artefact_or_error(container: Container, project_id: str, artefact_id: str, user: UserPublic):
    """Resolve an artefact for an edit and enforce the ACL: project access,
    project ownership of the artefact, and stage write permission."""
    await container.authz.assert_project_access(project_id, user)
    row = await container.db.get_artefact(artefact_id)
    if not row or row["project_id"] != project_id:
        raise SdlcError("NOT_FOUND", "Artefact not found")
    if not await container.chat.can_write_stage(project_id, int(row["phase"]), user):
        raise SdlcError("FORBIDDEN", "You do not have write permission on this stage")
    return row


@router.put("/api/projects/{project_id}/artefacts/{artefact_id}")
async def update_artefact(
    project_id: str, artefact_id: str, body: ArtefactUpdate,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    """In-place manual edit of an artefact by an authorised stage writer:
    documents and diagram source, saved instantly with a version bump."""
    row = await _writable_artefact_or_error(container, project_id, artefact_id, user)
    return await _save_artefact_edit(container, row, project_id, body.content, user, via="edit")


@router.post("/api/projects/{project_id}/artefacts/{artefact_id}/replace")
async def replace_artefact(
    project_id: str, artefact_id: str, request: Request,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    """Replace an artefact's content from an uploaded file — e.g. a
    `.drawio` edited in the desktop draw.io app, or a revised document. Same ACL
    and save path as the in-place edit; text files only (max 10 MB)."""
    from starlette.datastructures import UploadFile as StarletteUploadFile

    row = await _writable_artefact_or_error(container, project_id, artefact_id, user)
    form = await request.form()
    upload = form.get("file")
    if not isinstance(upload, StarletteUploadFile):
        raise SdlcError("VALIDATION_FAILED", "multipart field 'file' is required")
    raw = await upload.read()
    if len(raw) > 10_000_000:
        raise SdlcError("VALIDATION_FAILED", "file exceeds the 10 MB limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise SdlcError("VALIDATION_FAILED", "only text artefacts can be replaced (the file is not UTF-8 text)")
    return await _save_artefact_edit(container, row, project_id, text, user, via="replace")


# ------------------------------------------------------------------ diagram repair
_MERMAID_TYPES = {"HLD_DIAGRAM", "LLD_DIAGRAM"}
_MERMAID_EXTS = {".mmd", ".mermaid"}
_PLANTUML_EXTS = {".puml", ".plantuml", ".iuml"}


def _diagram_kind(row) -> str | None:  # noqa: ANN001
    """mermaid | plantuml | None for a stored artifact. Server-rendered SVGs
    (ARCH_DIAGRAM/COMPONENT_DIAGRAM) are not text-repairable and return None."""
    key = (row["storage_key"] or "").lower()
    dot = key.rfind(".")
    ext = key[dot:] if dot > 0 else ""
    if row["type"] in _MERMAID_TYPES or ext in _MERMAID_EXTS:
        return "mermaid"
    if row["type"] == "PLANTUML" or ext in _PLANTUML_EXTS:
        return "plantuml"
    return None


async def _llm_repair_diagram(container, kind: str, content: str, issues: list[str], mode: str) -> str:
    """Ask the model to fix syntax (preserving content) or redraw the diagram.
    Returns the raw candidate; the caller re-validates before persisting."""
    system_id = "diagram_repair.regenerate.system" if mode == "regenerate" else "diagram_repair.fix.system"
    detected = "\n".join(f"- {p}" for p in issues) or "(the renderer rejected it)"
    result = await container.llm.generate(
        intent="standard", tag="diagram_repair", temperature=0, max_tokens=2048,
        messages=[
            {"role": "system", "content": prompt_library.render(system_id, kind=kind)},
            {"role": "user", "content": prompt_library.render("diagram_repair.user", detected=detected, content=content)},
        ],
    )
    return result.content


@router.post("/api/projects/{project_id}/artefacts/{artefact_id}/repair")
async def repair_artefact(
    project_id: str, artefact_id: str, body: DiagramRepairRequest,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    """Repair a broken generated diagram from the viewer: deterministic
    syntax auto-fix first, then an LLM syntax-fix ('fix') or redraw ('regenerate')
    fallback. Saves a new version in place (audited); gated to stage writers. If
    the result still won't parse, nothing is overwritten and the issues are returned
    so the reviewer can re-run the whole stage instead."""
    await container.authz.assert_project_access(project_id, user)
    row = await container.db.get_artefact(artefact_id)
    if not row or row["project_id"] != project_id:
        raise SdlcError("NOT_FOUND", "Artefact not found")
    kind = _diagram_kind(row)
    if kind is None:
        raise SdlcError("VALIDATION_FAILED", "This artifact is not a text-based diagram that can be repaired")
    if not await container.chat.can_write_stage(project_id, int(row["phase"]), user):
        raise SdlcError("FORBIDDEN", "you do not have permission to repair diagrams on this stage")

    content = row["content"]
    if row["storage_key"]:
        stored = await container.content.get(row["storage_key"])
        if stored is not None:
            content = stored

    validate = cv.validate_mermaid if kind == "mermaid" else cv.validate_plantuml
    detected = validate(content)

    # 'fix': try the free deterministic repair first; only call the LLM if it can't
    # fully resolve. 'regenerate': always redraw via the LLM.
    via = "deterministic"
    corrected: str | None = None
    if body.mode == "fix":
        fixed, remaining = cv.repair_diagram(content, kind)
        if not remaining:
            corrected = fixed
    if corrected is None:
        try:
            candidate = await _llm_repair_diagram(container, kind, content, detected, body.mode)
        except SdlcError:
            raise
        cand_fixed, cand_remaining = cv.repair_diagram(candidate, kind)
        if cand_remaining:
            return {"ok": False, "issues": cand_remaining,
                    "message": "Automatic repair could not produce a valid diagram — re-run the stage to regenerate it."}
        corrected = cand_fixed
        via = "llm"

    if corrected == content:
        return {"ok": True, "changed": False, "version": row["version"],
                "message": "The diagram already parses; nothing to fix."}

    db_content = corrected if not row["storage_key"] else corrected[:2000]
    if row["storage_key"]:
        await container.content.put(row["storage_key"], corrected)
    version = await container.db.update_artefact_content(artefact_id, db_content)
    container.audit.record(
        project_id=project_id, phase=int(row["phase"]), agent_role="DiagramRepair",
        event="artifact.repaired", human_reviewer=user.email,
        detail={"artefactId": artefact_id, "kind": kind, "mode": body.mode, "via": via, "version": version},
    )
    return {"ok": True, "changed": True, "version": version, "via": via}


# MIME per stored extension so a downloaded artifact opens in the right
# desktop application (file-first artifacts).
_DOWNLOAD_MIME = {
    ".md": "text/markdown", ".json": "application/json", ".yaml": "application/yaml",
    ".yml": "application/yaml", ".mmd": "text/plain", ".puml": "text/plain",
    ".dsl": "text/plain", ".dbml": "text/plain", ".ts": "text/plain",
    ".drawio": "application/xml", # draw.io / diagrams.net editable diagram
    ".js": "text/javascript", ".py": "text/x-python", ".sql": "application/sql",
    ".txt": "text/plain", ".html": "text/html", ".css": "text/css",
    ".java": "text/plain", ".jmx": "application/xml",
}


def _file_response(content: str, filename: str) -> Response:
    dot = filename.rfind(".")
    ext = filename[dot:].lower() if dot > 0 else ""
    return Response(
        content=content.encode("utf-8"),
        media_type=_DOWNLOAD_MIME.get(ext, "application/octet-stream"),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/projects/{project_id}/artefacts/{artefact_id}/download")
async def artefact_download(
    project_id: str, artefact_id: str,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> Response:
    """Download the artifact as a real file (correct name + MIME) so the user
    can open it with an application installed on their desktop."""
    await container.authz.assert_project_access(project_id, user)
    row = await container.db.get_artefact(artefact_id)
    if not row or row["project_id"] != project_id:
        raise SdlcError("NOT_FOUND", "Artefact not found")
    content = row["content"]
    if row["storage_key"]:
        stored = await container.content.get(row["storage_key"])
        if stored is not None:
            content = stored
    filename = (row["storage_key"] or "").rsplit("/", 1)[-1] or f"{row['type']}-{row['id']}.txt"
    return _file_response(content, filename)


@router.get("/api/projects/{project_id}/quality-metrics")
async def project_quality_metrics(
    project_id: str, user: UserPublic = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Quality-metrics dashboard data: validator-score trend, first-pass approval
    vs. rework rate, issues caught at the gate, and stale-propagation churn —
    aggregated from the immutable audit trail (real signals, not estimates)."""
    await container.authz.assert_project_access(project_id, user)
    rows = await container.db.list_quality_events(project_id)

    scores: list[dict] = []          # chronological validator scores
    latest_by_phase: dict[int, dict] = {}
    dim_totals: dict[str, float] = {}
    dim_n = 0
    approvals = amends = pending = escalations = stale_flags = 0
    below_bar = 0

    for r in rows:
        ev = r["event"]
        phase = r["phase"]
        detail = r["detail"] or {}
        ts = r["timestamp"].isoformat() if r["timestamp"] else None
        if ev == "ai.validation":
            score = detail.get("score")
            if isinstance(score, (int, float)):
                entry = {"phase": phase, "score": int(score), "timestamp": ts,
                         "belowBar": bool(detail.get("belowBar")), "issues": detail.get("issues", 0)}
                scores.append(entry)
                if phase is not None:
                    latest_by_phase[phase] = entry
                if detail.get("belowBar"):
                    below_bar += 1
                dims = detail.get("dimensions") or {}
                if isinstance(dims, dict) and dims:
                    for k, v in dims.items():
                        if isinstance(v, (int, float)):
                            dim_totals[k] = dim_totals.get(k, 0.0) + float(v)
                    dim_n += 1
        elif ev in ("gate.approved", "gate.approved_override"):
            approvals += 1
        elif ev == "gate.amend_requested":
            amends += 1
        elif ev == "gate.pending_review":
            pending += 1
        elif ev == "stage.escalated":
            escalations += 1
        elif ev == "downstream.stale_flagged":
            stale_flags += 1

    graded = approvals + amends
    avg_score = round(sum(s["score"] for s in scores) / len(scores), 1) if scores else None
    dims_avg = {k: round(v / dim_n, 1) for k, v in dim_totals.items()} if dim_n else {}
    coverage_target = getattr(container.settings, "COVERAGE_MIN_PERCENT", 80)

    return {
        "projectId": project_id,
        "coverageTarget": coverage_target,
        "averageScore": avg_score,
        "latestScore": scores[-1]["score"] if scores else None,
        "belowBarCount": below_bar,
        "dimensionsAvg": dims_avg,
        "scoreTrend": scores[-50:],
        "perStageScore": [
            {"phase": p, "score": latest_by_phase[p]["score"], "belowBar": latest_by_phase[p]["belowBar"]}
            for p in sorted(latest_by_phase)
        ],
        "gate": {
            "approvals": approvals, "amendsRequested": amends, "pendingReview": pending,
            "escalations": escalations, "graded": graded,
            "firstPassRate": round(approvals / graded * 100, 1) if graded else None,
            "reworkRate": round(amends / graded * 100, 1) if graded else None,
        },
        "issuesCaughtAtGate": amends + escalations,
        "staleFlagged": stale_flags,
    }


@router.get("/api/projects/{project_id}/audit")
async def project_audit(
    project_id: str, user: UserPublic = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    await container.authz.assert_project_access(project_id, user)
    rows = await container.db.list_audit(project_id)
    return {"events": [
        {"id": e["id"], "timestamp": e["timestamp"].isoformat(), "projectId": e["project_id"],
         "phase": e["phase"], "agentRole": e["agent_role"], "event": e["event"],
         "provider": e["provider"], "model": e["model"], "promptTokens": e["prompt_tokens"],
         "completionTokens": e["completion_tokens"], "artefactHash": e["artefact_hash"],
         "humanReviewer": e["human_reviewer"], "detail": e["detail"]}
        for e in rows
    ]}


# ------------------------------------------------------------------ stage attachments
@router.post("/api/projects/{project_id}/phase/{phase_id}/attachments", status_code=201)
async def upload_attachment(
    project_id: str, phase_id: int, request: Request,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    """Attach a file to a stage's compose context. Text is decoded and
    stored for inlining into the prompt; binary is kept but flagged not-inlined."""
    from starlette.datastructures import UploadFile as StarletteUploadFile

    from ..repos.pg import new_id
    from ..services.content_store import attachment_key

    await container.authz.assert_project_access(project_id, user)
    if not 1 <= phase_id <= 12:
        raise SdlcError("VALIDATION_FAILED", "phaseId must be 1-12")
    form = await request.form()
    upload = form.get("file")
    if not isinstance(upload, StarletteUploadFile):
        raise SdlcError("VALIDATION_FAILED", "multipart field 'file' is required")
    raw = await upload.read()
    if len(raw) > 10_000_000:
        raise SdlcError("VALIDATION_FAILED", "attachment exceeds the 10 MB limit")
    filename = upload.filename or "attachment"
    content_type = upload.content_type or "application/octet-stream"

    # Extract usable context: documents (PDF/DOCX) are parsed, images are read by
    # a vision LLM (preferred) or OCR, text is decoded — so the attachment
    # becomes inlineable context.
    from ..services import attachment_extract as ax

    text, kind, note = ax.extract(raw, filename, content_type)
    method = "ocr" if kind == ax.KIND_IMAGE else "parse"

    # Images: prefer the multi-model vision LLM (Bedrock → Gemini) which both
    # transcribes text and describes structure; keep the deterministic OCR result
    # as the fallback (and append it verbatim for exactness when the LLM served).
    vision_mode = container.settings.ATTACHMENT_VISION
    if kind == ax.KIND_IMAGE and vision_mode != "ocr" and container.llm is not None:
        vis = await ax.describe_image_llm(
            raw, content_type, llm=container.llm,
            max_edge=container.settings.ATTACHMENT_VISION_MAX_EDGE,
        )
        if vis is not None:
            vtext, provider = vis
            ocr = text  # deterministic OCR from ax.extract above
            combined = (
                f"{vtext}\n\n---\nOCR (verbatim, deterministic):\n{ocr}" if ocr else vtext
            )
            text, note, method = combined[:200_000], f"vision:{provider}", f"vision:{provider}"

    is_text = bool(text)

    attachment_id = new_id()
    key = attachment_key(project_id, phase_id, attachment_id, filename)
    if is_text:
        await container.content.put(key, text)
    await container.db.insert_attachment(
        attachment_id=attachment_id, project_id=project_id, phase=phase_id, filename=filename,
        content_type=content_type, size_bytes=len(raw), is_text=is_text,
        storage_key=key, created_by=user.id,
    )
    container.audit.record(
        project_id=project_id, phase=phase_id, agent_role="Orchestrator",
        event="attachment.uploaded", human_reviewer=user.email,
        detail={"filename": filename, "bytes": len(raw), "kind": kind,
                "extractedChars": len(text), "method": method, "note": note},
    )
    return {"id": attachment_id, "filename": filename, "sizeBytes": len(raw),
            "isText": is_text, "kind": kind, "extractedChars": len(text),
            "method": method, "note": note}


@router.get("/api/projects/{project_id}/phase/{phase_id}/attachments")
async def list_attachments(
    project_id: str, phase_id: int,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    await container.authz.assert_project_access(project_id, user)
    rows = await container.db.list_attachments(project_id, phase_id)
    return {"attachments": [
        {"id": r["id"], "filename": r["filename"], "sizeBytes": r["size_bytes"],
         "isText": r["is_text"], "createdAt": r["created_at"].isoformat()}
        for r in rows
    ]}


@router.delete("/api/projects/{project_id}/phase/{phase_id}/attachments/{attachment_id}")
async def delete_attachment(
    project_id: str, phase_id: int, attachment_id: str,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    await container.authz.assert_project_access(project_id, user)
    row = await container.db.delete_attachment(attachment_id)
    if row and row["storage_key"]:
        try:
            await container.content.put(row["storage_key"], "") # tombstone the body ( pattern)
        except Exception:  # noqa: BLE001 — content-store purge is best-effort
            pass
    return {"ok": True}


# ------------------------------------------------------------------ codebase
@router.post("/api/projects/{project_id}/codebase", status_code=201)
async def upload_codebase(
    project_id: str, request: Request,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    # NB: request.form() yields starlette UploadFile (the PARENT of fastapi's
    # subclass) — an isinstance check against fastapi.UploadFile always fails.
    from starlette.datastructures import UploadFile as StarletteUploadFile

    await container.authz.assert_project_access(project_id, user)
    form = await request.form()
    upload = form.get("file")
    if not isinstance(upload, StarletteUploadFile):
        raise SdlcError("VALIDATION_FAILED", "multipart field 'file' (a .zip archive) is required")
    payload = await upload.read()
    result = await container.codebase.ingest_zip(project_id, user.id, payload)
    container.audit.record(
        project_id=project_id, agent_role="Orchestrator", event="codebase.uploaded",
        human_reviewer=user.email,
        detail={"files": result["files"], "archive": upload.filename},
    )
    return result


@router.get("/api/projects/{project_id}/codebase")
async def list_codebase(
    project_id: str,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    await container.authz.assert_project_access(project_id, user)
    rows = await container.db.list_codebase_files(project_id)
    return {"files": [
        {"id": r["id"], "path": r["path"], "sizeBytes": r["size_bytes"],
         "uploadedAt": r["uploaded_at"].isoformat()}
        for r in rows
    ]}


# ------------------------------------------------------------------ file explorer
@router.get("/api/projects/{project_id}/files")
async def project_files(
    project_id: str,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    """The dedicated storage layer as a browsable folder tree: one folder per
    phase (files named TYPE-<id>.<ext> exactly as stored on disk / in S3),
    plus the uploaded existing codebase under codebase/."""
    await container.authz.assert_project_access(project_id, user)

    artefacts = await container.db.list_artefacts(project_id)
    folders: dict[str, list[dict]] = {}
    for a in artefacts:
        key = a["storage_key"]
        folder = f"phase-{a['phase']}"
        name = key.rsplit("/", 1)[-1] if key else f"{a['type']}-{a['id']}"
        dot = name.rfind(".")
        folders.setdefault(folder, []).append({
            "name": name,
            "ext": name[dot:] if dot > 0 else "",
            "artefactId": a["id"],
            "type": a["type"],
            "title": a["title"],
            "storageKey": key,
            "storageMode": a["storage_mode"],
            "createdAt": a["created_at"].isoformat(),
        })

    codebase = await container.db.list_codebase_files(project_id)
    tree = [
        {"name": f"phase-{n}", "kind": "phase", "phase": n, "files": sorted(folders.get(f"phase-{n}", []), key=lambda f: f["name"])}
        for n in range(1, 13) if folders.get(f"phase-{n}")
    ]
    if codebase:
        tree.append({
            "name": "codebase", "kind": "codebase", "phase": None,
            "files": [
                {"name": c["path"], "ext": ("." + c["path"].rsplit(".", 1)[-1]) if "." in c["path"] else "",
                 "codebaseFileId": c["id"], "sizeBytes": c["size_bytes"],
                 "uploadedAt": c["uploaded_at"].isoformat()}
                for c in codebase
            ],
        })
    return {"root": f"content-store/{project_id}", "storageMode": container.content.mode, "folders": tree}


@router.get("/api/projects/{project_id}/codebase/{file_id}")
async def codebase_file_content(
    project_id: str, file_id: str,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    await container.authz.assert_project_access(project_id, user)
    row = await container.db.get_codebase_file(project_id, file_id)
    if not row:
        raise SdlcError("NOT_FOUND", "Codebase file not found")
    return {"file": {"id": row["id"], "path": row["path"], "content": row["content"],
                     "sizeBytes": row["size_bytes"], "uploadedAt": row["uploaded_at"].isoformat()}}


@router.get("/api/projects/{project_id}/codebase/{file_id}/download")
async def codebase_file_download(
    project_id: str, file_id: str,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> Response:
    await container.authz.assert_project_access(project_id, user)
    row = await container.db.get_codebase_file(project_id, file_id)
    if not row:
        raise SdlcError("NOT_FOUND", "Codebase file not found")
    return _file_response(row["content"], row["path"].rsplit("/", 1)[-1])


# ------------------------------------------------------------------ Project Canon
@router.get("/api/projects/{project_id}/canon")
async def list_canon(
    project_id: str, includeInactive: bool = True,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    """The project's binding rules/decisions/glossary. Readable by any member."""
    entries = await container.canon.list(project_id, user, active_only=not includeInactive)
    can_author = True
    try:
        await container.canon.assert_can_author(project_id, user)
    except SdlcError:
        can_author = False
    return {"entries": entries, "canAuthor": can_author}


@router.post("/api/projects/{project_id}/canon")
async def create_canon(
    project_id: str, body: dict,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    return {"entry": await container.canon.create(project_id, user, body)}


@router.patch("/api/projects/{project_id}/canon/{entry_id}")
async def update_canon(
    project_id: str, entry_id: str, body: dict,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    return {"entry": await container.canon.update(project_id, entry_id, user, body)}


@router.delete("/api/projects/{project_id}/canon/{entry_id}")
async def delete_canon(
    project_id: str, entry_id: str,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    await container.canon.delete(project_id, entry_id, user)
    return {"deleted": True}


@router.get("/api/projects/{project_id}/canon/preview")
async def preview_canon(
    project_id: str, stage: int | None = None,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    """Exactly the Canon + Formwork text the agents will receive — no surprises
    about what is shaping generation."""
    await container.authz.assert_project_access(project_id, user)
    canon_block = await container.canon.render_block(project_id, stage)
    produces: list[str] = []
    if stage:
        from ..domain.models import get_phase

        produces = list(get_phase(stage).produces)
    else:
        for p in range(1, 7):
            from ..domain.models import get_phase

            produces.extend(get_phase(p).produces)
    formwork_block = await container.formworks.render_block(project_id, produces)
    return {"canonBlock": canon_block, "formworkBlock": formwork_block,
            "chars": len(canon_block) + len(formwork_block)}


# ------------------------------------------------------------------ Formwork Library
@router.get("/api/projects/{project_id}/formworks")
async def list_project_formworks(
    project_id: str,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    """Templates in scope for this project (project-scoped shadow platform-wide)."""
    return {"formworks": await container.formworks.list(project_id, user)}


@router.post("/api/projects/{project_id}/formworks")
async def upload_project_formwork(
    project_id: str, body: dict,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    return {"formwork": await container.formworks.upload(project_id, user, body)}


@router.get("/api/formworks")
async def list_platform_formworks(
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    """The shared, platform-wide template library (SUPER_ADMIN)."""
    return {"formworks": await container.formworks.list(None, user)}


@router.post("/api/formworks")
async def publish_platform_formwork(
    body: dict,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    return {"formwork": await container.formworks.upload(None, user, body)}


@router.delete("/api/formworks/{formwork_id}")
async def retire_formwork(
    formwork_id: str,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    await container.formworks.remove(formwork_id, user)
    return {"retired": True}


# ------------------------------------------------------------------ governance (, Responsible AI)
@router.get("/api/governance/guardrails")
async def governance_guardrails(_user: UserPublic = Depends(current_user)) -> dict:
    """Read-only inventory of every input/output guardrail rule and where it
    is enforced — the platform's Responsible AI policy is auditable, not
    implicit."""
    return guardrails_svc.inventory()


@router.get("/api/governance/prompts")
async def governance_prompts(_user: UserPublic = Depends(current_user)) -> dict:
    """Read-only prompt library: every prompt the platform sends to an LLM,
    with id, version and description. No inline prompts exist outside it."""
    return prompt_library.inventory()


@router.get("/api/governance/skills")
async def governance_skills(_user: UserPublic = Depends(current_user)) -> dict:
    """Read-only skill packs: every skill is a markdown file — the
    frontmatter (identity, RBAC roles, tier, execution wiring) plus the
    markdown instruction body, exactly as loaded at boot."""
    from ..services.skills import SKILL_PACKS

    return {
        "count": len(SKILL_PACKS),
        "skills": [
            {
                "id": p["id"], "name": p["name"], "description": p["description"],
                "file": p["file"], "phase": p.get("phase"), "roles": p["roles"],
                "tier": p["tier"], "executor": p["executor"],
                "tools": p.get("tools") or [], "markdown": p["body"],
            }
            for p in SKILL_PACKS
        ],
    }


# ------------------------------------------------------------------ observability
def _require_admin(user: UserPublic) -> None:
    if user.role != "SUPER_ADMIN":
        raise SdlcError("FORBIDDEN", "Observability dashboard requires SUPER_ADMIN")


@router.get("/api/observability/summary")
async def observability_summary(
    days: int = 7,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    """AI usage aggregates: calls, tokens, latency (avg/p95), error rate,
    estimated cost — total, per provider, per day, per MCP tool."""
    _require_admin(user)
    return await container.telemetry.summary(days=max(1, min(days, 90)))


@router.get("/api/observability/traces")
async def observability_traces(
    limit: int = 100, projectId: str | None = None,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    """Most recent LLM/tool spans (provider, model, tier, tokens, latency,
    status) — the drill-down behind the dashboard tiles."""
    _require_admin(user)
    rows = await container.telemetry.traces(limit=max(1, min(limit, 500)), project_id=projectId)
    return {
        "traces": [
            {
                "ts": r["ts"].isoformat(), "projectId": r["project_id"], "stage": r["stage"],
                "kind": r["kind"], "provider": r["provider"], "model": r["model"],
                "tier": r["tier"], "tag": r["tag"],
                "promptTokens": r["prompt_tokens"], "completionTokens": r["completion_tokens"],
                "latencyMs": r["latency_ms"], "status": r["status"], "error": r["error"],
                "costUsd": float(r["cost_usd"]),
            }
            for r in rows
        ]
    }


@router.get("/metrics")
async def prometheus_metrics(container: Container = Depends(get_container)) -> PlainTextResponse:
    """Prometheus text exposition for local monitoring servers (Prometheus/
    Grafana scrape). Unauthenticated by design — expose it cluster-internally
    only, like any /metrics endpoint."""
    s = await container.telemetry.summary(days=1)
    t = s["totals"]
    lines = [
        "# HELP sdlc_llm_calls_total LLM calls in the last 24h",
        "# TYPE sdlc_llm_calls_total gauge",
        f"sdlc_llm_calls_total {int(t.get('llm_calls', 0))}",
        "# HELP sdlc_tool_calls_total MCP tool calls in the last 24h",
        "# TYPE sdlc_tool_calls_total gauge",
        f"sdlc_tool_calls_total {int(t.get('tool_calls', 0))}",
        "# HELP sdlc_llm_tokens_total Tokens in the last 24h by direction",
        "# TYPE sdlc_llm_tokens_total gauge",
        f'sdlc_llm_tokens_total{{direction="prompt"}} {int(t.get("prompt_tokens", 0))}',
        f'sdlc_llm_tokens_total{{direction="completion"}} {int(t.get("completion_tokens", 0))}',
        "# HELP sdlc_llm_latency_ms LLM latency (last 24h)",
        "# TYPE sdlc_llm_latency_ms gauge",
        f'sdlc_llm_latency_ms{{stat="avg"}} {float(t.get("avg_latency_ms", 0)):.1f}',
        f'sdlc_llm_latency_ms{{stat="p95"}} {float(t.get("p95_latency_ms", 0)):.1f}',
        "# HELP sdlc_llm_errors_total Failed spans in the last 24h",
        "# TYPE sdlc_llm_errors_total gauge",
        f"sdlc_llm_errors_total {int(t.get('errors', 0))}",
        "# HELP sdlc_llm_cost_usd Estimated LLM spend in the last 24h",
        "# TYPE sdlc_llm_cost_usd gauge",
        f"sdlc_llm_cost_usd {float(t.get('cost_usd', 0)):.6f}",
    ]
    for p in s["providers"]:
        lines.append(
            f'sdlc_llm_provider_calls_total{{provider="{p["provider"]}"}} {int(p["calls"])}'
        )
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


# ------------------------------------------------------------------ provider status (live vs mock)
@router.get("/api/providers")
async def provider_status(
    _user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    import httpx

    llm_providers: list = []
    tool_modes: dict = {}
    gen_mode = "unknown"
    effective_mock = True
    active_providers: list = []
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(f"{container.settings.AI_CLIENT_URL}/v1/providers")
            body = res.json()
            llm_providers = body.get("providers", [])
            gen_mode = body.get("mode", "unknown")
            effective_mock = bool(body.get("effectiveMock", True))
            active_providers = body.get("activeProviders", [])
    except Exception:
        pass
    try:
        tools_base = container.settings.TOOLS_MCP_URL.rsplit("/mcp", 1)[0]
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(f"{tools_base}/readyz")
            tool_modes = res.json().get("modes", {})
    except Exception:
        pass
    return {
        "llm": llm_providers, "tools": tool_modes, "authMode": container.settings.AUTH_MODE,
        # Generation mode: mock | llm | auto, whether the mock is actually
        # serving, and the multimodel roster of real providers with credentials.
        "generationMode": gen_mode, "effectiveMock": effective_mock,
        "activeProviders": active_providers,
    }


# ------------------------------------------------------------------ KB (RAG) & skills
@router.get("/api/kb/search")
async def kb_search(
    q: str = "", projectId: str | None = None,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    if projectId:
        await container.authz.assert_project_access(projectId, user)
    snippets = await container.rag.retrieve(q or "enterprise standards", projectId, top_k=8)
    return {
        "standards": [s for s in snippets if s["source"] == "standard"],
        "artefacts": [s for s in snippets if s["source"] == "artifact"],
        "codebase": [s for s in snippets if s["source"] == "codebase"],
    }


@router.get("/api/skills")
async def skills(container: Container = Depends(get_container)) -> dict:
    try:
        return {"tools": await container.mcp.list_tools()}
    except Exception:
        return {"tools": [], "degraded": True}


# ------------------------------------------------------------------ project skills
@router.get("/api/projects/{project_id}/skills")
async def project_skills(
    project_id: str, phase: int | None = None,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    await container.authz.assert_project_access(project_id, user)
    project = await container.db.get_project(project_id)
    if not project:
        raise SdlcError("NOT_FOUND", "Project not found")
    return {
        "currentPhase": project["current_phase"],
        "skills": await container.skills.list_for(project_id, project["current_phase"], user, phase),
    }


@router.post("/api/projects/{project_id}/skills/{skill_id}/execute")
async def execute_skill(
    project_id: str, skill_id: str, body: dict | None = None,
    user: UserPublic = Depends(current_user), container: Container = Depends(get_container),
) -> dict:
    await container.authz.assert_project_access(project_id, user)
    user_input = str((body or {}).get("input", ""))
    return await container.skills.execute(project_id, skill_id, user, user_input)


# ------------------------------------------------------------------ GitHub webhook
@router.post("/api/webhooks/github")
async def github_webhook(request: Request, container: Container = Depends(get_container)):
    raw = await request.body()
    signature = request.headers.get("x-hub-signature-256", "")
    expected = "sha256=" + hmac_mod.new(
        container.settings.GITHUB_WEBHOOK_SECRET.encode(), raw, hashlib.sha256
    ).hexdigest()
    if not signature or not hmac_mod.compare_digest(expected, signature):
        raise SdlcError("AUTH_FAILED", "Webhook signature mismatch")
    payload = await request.json()
    run = payload.get("workflow_run") or {}
    if payload.get("action") == "completed" and run.get("id") is not None:
        await container.monitor.on_webhook_run_completed(str(run["id"]))
        return JSONResponse({"processed": True})
    return JSONResponse({"ignored": True})
