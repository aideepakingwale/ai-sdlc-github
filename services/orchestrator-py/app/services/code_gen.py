"""Two-step code generation: approval of the proposed structure, the post-commit record, the explorer view and
the .zip download. The generation itself lives in app/agents/code_generation.py; the gate calls in here.

Authorisation: the structure and the code are approved through the stage's normal gate (its reviewers), so the
same RBAC and sign-off matrix apply. Reading the plan/files and downloading the zip need project read access;
re-planning the structure needs stage write access. Everything is recorded in the audit trail.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from ..domain.errors import SdlcError
from ..domain.models import UserPublic
from ..repos.aws import DynamoStore
from ..repos.pg import Database, new_id
from . import code_structure as cs
from .audit import AuditService
from .authz import AuthzService
from .content_store import ContentStore

log = logging.getLogger("code_gen")
CODE_TYPES = ("APP_CODE", "UNIT_TESTS")
IMPLEMENTATION_TEMPLATE = 6


class CodeGenService:
    def __init__(
        self, db: Database, dynamo: DynamoStore, audit: AuditService, authz: AuthzService, content: ContentStore,
        workflow: Any, enqueue: Callable[[str, int, str], Awaitable[Any]], can_write: Callable[[str, int, UserPublic], Awaitable[bool]],
        enabled: bool = True,
    ) -> None:
        self._db, self._dynamo, self._audit, self._authz = db, dynamo, audit, authz
        self._content, self._workflow, self._enqueue, self._can_write = content, workflow, enqueue, can_write
        self._enabled = enabled

    # ------------------------------------------------------------------ gate hooks
    def applies(self, stage: dict[str, Any]) -> bool:
        return self._enabled and int(stage.get("template") or 0) == IMPLEMENTATION_TEMPLATE

    async def structure_pending(self, project_id: str, phase: int, stage: dict[str, Any]) -> dict[str, Any] | None:
        """The proposal awaiting approval when the stage is at its STRUCTURE review, else None."""
        if not self.applies(stage):
            return None
        plan = await self._db.latest_code_plan(project_id, phase)
        return plan if plan and plan["status"] == "proposed" else None

    async def approve_structure(self, *, project_id: str, phase: int, stage: dict[str, Any], plan: dict[str, Any],
                                user: UserPublic, override: bool) -> dict[str, Any]:
        """The reviewers approved the structure: record it, release the stage from its gate and start step 2."""
        approved = await self._db.approve_code_plan(plan["id"], approver=user.email)
        if not approved:
            raise SdlcError("GATE_CONFLICT", "This structure was already decided")
        await self._dynamo.transition_phase_state(
            project_id=project_id, phase=phase, expected="PENDING_REVIEW", next_status="IN_PROGRESS", reviewed_by=user.email)
        await self._db.clear_phase_signoffs(project_id, phase)
        self._audit.record(
            project_id=project_id, phase=phase, agent_role="GateController", event="code.structure.approved",
            human_reviewer=user.email,
            detail={"version": plan["version"], "files": len(plan["structure"]["files"]), "hash": cs.structure_hash(plan["structure"]),
                    "stage": stage["key"], "superAdminOverride": override, "artefactId": plan.get("artefact_id")})
        job = await self._enqueue(project_id, phase, user.email)
        self._audit.record(project_id=project_id, phase=phase, agent_role="GateController", event="code.generation.queued",
                           human_reviewer=user.email, detail={"version": plan["version"], "job": (job or {}).get("jobId")})
        return {"projectId": project_id, "phase": phase, "status": "IN_PROGRESS", "nextPhase": None,
                "structureApproved": True, "codeGeneration": "started", "version": plan["version"]}

    async def after_publish(self, *, project_id: str, phase: int, stage: dict[str, Any], results: list[dict[str, Any]],
                            user: UserPublic) -> None:
        """The approved code was published: record the commit and the pull request (best-effort: the approval stands)."""
        if not self.applies(stage):
            return
        try:
            plan = await self._db.latest_code_plan(project_id, phase)
            commit = next((r for r in results if r.get("tool") == "github_commit_code"), None)
            pr = next((r for r in results if r.get("tool") == "github_create_pull_request"), None)
            if not plan or not commit:
                return
            await self._db.mark_code_plan_committed(plan["id"], str(commit.get("ref") or commit.get("url") or "committed"))
            if pr and pr.get("url"):
                title = f"PR #{pr.get('prNumber') or ''}: {plan['meta'].get('prTitle', 'Implementation')}".replace("#:", "#")
                await self._db.pool.execute(  # type: ignore[union-attr]
                    "INSERT INTO artefacts (id, project_id, phase, type, title, content, url) VALUES ($1,$2,$3,'PULL_REQUEST',$4,$5,$6)",
                    new_id(), project_id, phase, title, plan["meta"].get("prBody", ""), pr["url"])
            self._audit.record(
                project_id=project_id, phase=phase, agent_role="Publisher", event="code.committed", human_reviewer=user.email,
                detail={"version": plan["version"], "branch": plan["meta"].get("branch"), "commit": commit.get("ref"),
                        "commitUrl": commit.get("url"), "pullRequest": (pr or {}).get("url"), "files": len(plan["structure"]["files"])})
        except Exception:  # noqa: BLE001 - never undo an approval over bookkeeping
            log.warning("could not record the code commit", exc_info=True)

    # ------------------------------------------------------------------ views
    async def _stage(self, project_id: str, phase: int) -> dict[str, Any]:
        wf = await self._workflow.view(project_id)
        stage = next((s for s in wf["stages"] if s["seq"] == phase), None)
        if not stage:
            raise SdlcError("NOT_FOUND", f"No stage at position {phase}")
        return stage

    async def _files(self, project_id: str, phase: int) -> dict[str, dict[str, Any]]:
        rows = await self._db.list_phase_artefacts(project_id, phase)
        return {r["title"]: dict(r) for r in rows if r["type"] in CODE_TYPES and r["is_latest"]}

    async def view(self, *, project_id: str, phase: int, user: UserPublic) -> dict[str, Any]:
        """Everything the explorer needs: status, the approved/proposed structure as a tree with per-file
        generation state, and the commit info. File contents are fetched per file via the artifact endpoint."""
        await self._authz.assert_project_access(project_id, user)
        stage = await self._stage(project_id, phase)
        if not self.applies(stage):
            return {"enabled": False, "status": "none"}
        plan = await self._db.latest_code_plan(project_id, phase)
        if not plan:
            return {"enabled": True, "status": "none", "checkpoint": None, "canReset": False}
        files = await self._files(project_id, phase)
        structure = plan["structure"]
        generated = {p for p in files if p in {f["path"] for f in structure["files"]}}
        status = ("committed" if plan.get("committed_at") else "implemented" if plan.get("implemented_at")
                  else plan["status"])
        flat = [{**f, "generated": f["path"] in generated, "artefactId": (files.get(f["path"]) or {}).get("id"),
                 "chars": len((files.get(f["path"]) or {}).get("content") or "")} for f in structure["files"]]
        return {
            "enabled": True, "status": status, "version": plan["version"], "checkpoint": "structure" if plan["status"] == "proposed" else "code",
            "structure": {"summary": structure.get("summary", ""), "conventions": structure.get("conventions", []),
                          "directories": structure.get("directories", [])},
            "tree": cs.build_tree(structure, generated), "files": flat, "branch": plan["meta"].get("branch"),
            "commitMessage": plan["meta"].get("commitMessage"), "artefactId": plan.get("artefact_id"),
            "decidedBy": plan.get("decided_by"), "decidedAt": plan["decided_at"].isoformat() if plan.get("decided_at") else None,
            "implementedAt": plan["implemented_at"].isoformat() if plan.get("implemented_at") else None,
            "committedAt": plan["committed_at"].isoformat() if plan.get("committed_at") else None, "commitRef": plan.get("commit_ref"),
            "generatedCount": len(generated), "fileCount": len(structure["files"]),
            "canReset": await self._can_write(project_id, phase, user) and not plan.get("committed_at"),
        }

    async def reset(self, *, project_id: str, phase: int, user: UserPublic, reason: str = "") -> dict[str, Any]:
        """Throw away the current structure (and approval) so the next run proposes a new one. Writers only;
        not after the code was committed."""
        stage = await self._stage(project_id, phase)
        if not self.applies(stage):
            raise SdlcError("VALIDATION_FAILED", "this stage does not use two-step code generation")
        if not await self._can_write(project_id, phase, user):
            raise SdlcError("FORBIDDEN", f"Re-planning the code structure of '{stage['name']}' needs write permission on the stage")
        plan = await self._db.latest_code_plan(project_id, phase)
        if not plan:
            raise SdlcError("NOT_FOUND", "There is no code structure to replace yet")
        if plan.get("committed_at"):
            raise SdlcError("GATE_CONFLICT", "The code of this structure was already committed to GitHub")
        await self._db.supersede_code_plan(plan["id"])
        self._audit.record(project_id=project_id, phase=phase, agent_role="CodeGeneration", event="code.structure.reset",
                           human_reviewer=user.email, detail={"version": plan["version"], "reason": reason[:300]})
        return {"ok": True, "status": "none"}

    async def zip(self, *, project_id: str, phase: int, user: UserPublic) -> tuple[bytes, str]:
        """The whole generated codebase as one .zip (a single project folder). Project read access."""
        await self._authz.assert_project_access(project_id, user)
        stage = await self._stage(project_id, phase)
        plan = await self._db.latest_code_plan(project_id, phase) if self.applies(stage) else None
        files = await self._files(project_id, phase)
        wanted = {f["path"] for f in plan["structure"]["files"]} if plan else set(files)
        contents: dict[str, str] = {}
        for path, row in files.items():
            if path not in wanted:
                continue
            body = row["content"] or ""
            if row.get("storage_key"):
                stored = await self._content.get(row["storage_key"])
                if stored is not None:
                    body = stored
            contents[path] = body
        if not contents:
            raise SdlcError("NOT_FOUND", "No code has been generated for this stage yet")
        project = await self._db.get_project(project_id)
        name = (project or {}).get("name") or "codebase"
        data = cs.build_zip(contents, root=name)
        self._audit.record(project_id=project_id, phase=phase, agent_role="CodeGeneration", event="code.zip.downloaded",
                           human_reviewer=user.email, detail={"files": len(contents), "bytes": len(data)})
        slug = "".join(c if c.isalnum() or c in "-_." else "-" for c in name).strip("-") or "codebase"
        return data, f"{slug}.zip"
