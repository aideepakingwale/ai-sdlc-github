"""Project Canon — the project's binding truth.

A Canon entry is a durable, human-authored statement that every agent must
honour when generating artifacts: an architectural rule, a recorded decision,
a domain glossary term, a hard constraint or a team preference. Entries are
composed into a prompt block injected into every phase agent (and optionally
scoped to a single stage template), so guidance survives across stages,
regenerations and amend cycles — unlike chat, which is per-turn.

Ordering is deliberate: MUST entries first, then SHOULD, then CONTEXT, so the
model reads non-negotiables before nice-to-haves.

Authoring RBAC ("PM / architects / authorised users"):
  - SUPER_ADMIN                      — always
  - the project's managing PM        — the creator
  - members with SA or TA membership — the architects
Everyone with project access can READ the canon.
"""

from __future__ import annotations

from typing import Any, Literal

from ..domain.errors import SdlcError
from ..domain.models import UserPublic

CanonCategory = Literal["rule", "decision", "glossary", "constraint", "preference"]
CanonPriority = Literal["must", "should", "context"]

CATEGORIES = ("rule", "decision", "glossary", "constraint", "preference")
PRIORITIES = ("must", "should", "context")

AUTHOR_MEMBERSHIPS = ("SA", "TA")

_PRIORITY_LABEL = {
    "must": "MUST (non-negotiable)",
    "should": "SHOULD (strong preference)",
    "context": "CONTEXT (background)",
}


class CanonService:
    def __init__(self, db: Any, authz: Any, audit: Any) -> None:
        self._db = db
        self._authz = authz
        self._audit = audit

    # ------------------------------------------------------------------ authz
    async def assert_can_author(self, project_id: str, user: UserPublic) -> None:
        if user.role == "SUPER_ADMIN":
            return
        project = await self._db.get_project(project_id)
        if not project:
            raise SdlcError("NOT_FOUND", "Project not found")
        if user.role == "PROJECT_MANAGER":
            if project["created_by"] != user.id:
                raise SdlcError("FORBIDDEN", "Only the managing PM can edit this project's Canon")
            return
        membership = await self._authz.get_membership_role(project_id, user.id)
        if membership not in AUTHOR_MEMBERSHIPS:
            raise SdlcError(
                "FORBIDDEN",
                "Canon authoring requires the managing Project Manager, a Solution/Technical "
                "Architect on this project, or SUPER_ADMIN",
            )

    # ------------------------------------------------------------------ CRUD
    async def list(self, project_id: str, user: UserPublic, *, active_only: bool = False) -> list[dict]:
        await self._authz.assert_project_access(project_id, user)
        rows = await self._db.list_canon(project_id, active_only=active_only)
        return [self._row(r) for r in rows]

    async def create(self, project_id: str, user: UserPublic, payload: dict) -> dict:
        await self._authz.assert_project_access(project_id, user)
        await self.assert_can_author(project_id, user)
        category = payload.get("category", "rule")
        priority = payload.get("priority", "must")
        if category not in CATEGORIES:
            raise SdlcError("VALIDATION_FAILED", f"category must be one of {list(CATEGORIES)}")
        if priority not in PRIORITIES:
            raise SdlcError("VALIDATION_FAILED", f"priority must be one of {list(PRIORITIES)}")
        title = (payload.get("title") or "").strip()
        body = (payload.get("body") or "").strip()
        if len(title) < 3 or not body:
            raise SdlcError("VALIDATION_FAILED", "Canon entries need a title (3+ chars) and a body")
        stage = payload.get("stage")
        if stage is not None and not (isinstance(stage, int) and 1 <= stage <= 6):
            raise SdlcError("VALIDATION_FAILED", "stage must be 1-6 or null (all stages)")

        row = await self._db.insert_canon(
            project_id=project_id, category=category, priority=priority, stage=stage,
            title=title, body=body, user_id=user.id,
        )
        self._audit.record(
            project_id=project_id, phase=stage, agent_role="Canon", event="canon.created",
            human_reviewer=user.email, artefact_body=body,
            detail={"category": category, "priority": priority, "title": title},
        )
        return self._row(row)

    async def update(self, project_id: str, entry_id: str, user: UserPublic, patch: dict) -> dict:
        await self._authz.assert_project_access(project_id, user)
        await self.assert_can_author(project_id, user)
        row = await self._db.update_canon(entry_id, project_id, patch)
        if not row:
            raise SdlcError("NOT_FOUND", "Canon entry not found")
        self._audit.record(
            project_id=project_id, agent_role="Canon", event="canon.updated",
            human_reviewer=user.email, detail={"entryId": entry_id, "fields": sorted(patch)},
        )
        return self._row(row)

    async def delete(self, project_id: str, entry_id: str, user: UserPublic) -> None:
        await self._authz.assert_project_access(project_id, user)
        await self.assert_can_author(project_id, user)
        if not await self._db.delete_canon(entry_id, project_id):
            raise SdlcError("NOT_FOUND", "Canon entry not found")
        self._audit.record(
            project_id=project_id, agent_role="Canon", event="canon.deleted",
            human_reviewer=user.email, detail={"entryId": entry_id},
        )

    # ------------------------------------------------------------------ prompt block
    async def render_block(self, project_id: str, stage_template: int | None = None) -> str:
        """The Canon block injected into agent prompts. Empty string when the
        project has no active entries (prompt stays unchanged)."""
        rows = await self._db.list_canon(project_id, active_only=True)
        entries = [
            r for r in rows
            if r["stage"] is None or stage_template is None or r["stage"] == stage_template
        ]
        if not entries:
            return ""

        lines = [
            "## PROJECT CANON (binding — authored by the project's PM/architects)",
            "These statements govern this project. Honour every MUST; do not contradict them, "
            "and do not restate them as findings. Where the Canon conflicts with your defaults, "
            "the Canon wins.",
        ]
        for priority in PRIORITIES:
            group = [e for e in entries if e["priority"] == priority]
            if not group:
                continue
            lines.append(f"\n### {_PRIORITY_LABEL[priority]}")
            for e in group:
                scope = "" if e["stage"] is None else f" _(stage {e['stage']} only)_"
                lines.append(f"- **[{e['category']}] {e['title']}**{scope}: {e['body']}")
        return "\n".join(lines)

    @staticmethod
    def _row(r: Any) -> dict:
        return {
            "id": r["id"], "category": r["category"], "priority": r["priority"],
            "stage": r["stage"], "title": r["title"], "body": r["body"], "active": r["active"],
            "createdBy": r["created_by"],
            "createdAt": r["created_at"].isoformat(), "updatedAt": r["updated_at"].isoformat(),
        }
