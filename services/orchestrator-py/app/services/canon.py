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
            title=title, body=body, user_id=user.id, origin=payload.get("origin"),
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

    # ------------------------------------------------------------------ organisation rules
    async def org_list(self, user: UserPublic, *, active_only: bool = False) -> list[dict]:
        rows = await self._db.list_org_canon(active_only=active_only)
        return [self._org_row(r) for r in rows]

    @staticmethod
    def _validate(payload: dict) -> tuple[str, str, int | None, str, str]:
        category = payload.get("category", "rule")
        priority = payload.get("priority", "must")
        if category not in CATEGORIES:
            raise SdlcError("VALIDATION_FAILED", f"category must be one of {list(CATEGORIES)}")
        if priority not in PRIORITIES:
            raise SdlcError("VALIDATION_FAILED", f"priority must be one of {list(PRIORITIES)}")
        title, body = (payload.get("title") or "").strip(), (payload.get("body") or "").strip()
        if len(title) < 3 or not body:
            raise SdlcError("VALIDATION_FAILED", "A rule needs a title (3+ characters) and a body")
        stage = payload.get("stage")
        if stage is not None and not (isinstance(stage, int) and 1 <= stage <= 6):
            raise SdlcError("VALIDATION_FAILED", "stage must be 1-6 or null (all stages)")
        return category, priority, stage, title, body

    @staticmethod
    def _assert_admin(user: UserPublic) -> None:
        if user.role != "SUPER_ADMIN":
            raise SdlcError("FORBIDDEN", "Only an administrator can change organisation rules")

    async def org_create(self, user: UserPublic, payload: dict) -> dict:
        self._assert_admin(user)
        category, priority, stage, title, body = self._validate(payload)
        row = await self._db.insert_org_canon(category=category, priority=priority, stage=stage, title=title, body=body, user_id=user.id)
        self._audit.record(project_id=None, agent_role="Canon", event="org_canon.created", human_reviewer=user.email, detail={"title": title, "priority": priority})
        return self._org_row(row)

    async def org_update(self, entry_id: str, user: UserPublic, patch: dict) -> dict:
        self._assert_admin(user)
        row = await self._db.update_org_canon(entry_id, patch)
        if not row:
            raise SdlcError("NOT_FOUND", "Organisation rule not found")
        self._audit.record(project_id=None, agent_role="Canon", event="org_canon.updated", human_reviewer=user.email, detail={"entryId": entry_id, "fields": sorted(patch)})
        return self._org_row(row)

    async def org_delete(self, entry_id: str, user: UserPublic) -> None:
        self._assert_admin(user)
        if not await self._db.delete_org_canon(entry_id):
            raise SdlcError("NOT_FOUND", "Organisation rule not found")
        self._audit.record(project_id=None, agent_role="Canon", event="org_canon.deleted", human_reviewer=user.email, detail={"entryId": entry_id})

    async def inherited(self, project_id: str, user: UserPublic) -> list[dict]:
        """The organisation's rules as this project sees them, with whether (and why) the project opted out."""
        await self._authz.assert_project_access(project_id, user)
        outs = {o["org_entry_id"]: o for o in await self._db.list_org_optouts(project_id)}
        res = []
        for r in await self._db.list_org_canon(active_only=True):
            row = self._org_row(r)
            o = outs.get(r["id"])
            res.append({**row, "optedOut": o is not None, "optOutReason": o["reason"] if o else "", "optedOutBy": o["opted_out_by"] if o else None})
        return res

    async def opt_out(self, project_id: str, entry_id: str, user: UserPublic, reason: str) -> None:
        await self._authz.assert_project_access(project_id, user)
        await self.assert_can_author(project_id, user)
        if len((reason or "").strip()) < 5:
            raise SdlcError("VALIDATION_FAILED", "Say why this project does not follow the organisation rule (5+ characters)")
        if not any(r["id"] == entry_id for r in await self._db.list_org_canon(active_only=False)):
            raise SdlcError("NOT_FOUND", "Organisation rule not found")
        await self._db.set_org_optout(project_id, entry_id, reason.strip(), user.email)
        self._audit.record(project_id=project_id, agent_role="Canon", event="org_canon.opted_out", human_reviewer=user.email, detail={"entryId": entry_id, "reason": reason.strip()[:300]})

    async def opt_in(self, project_id: str, entry_id: str, user: UserPublic) -> None:
        await self._authz.assert_project_access(project_id, user)
        await self.assert_can_author(project_id, user)
        await self._db.clear_org_optout(project_id, entry_id)
        self._audit.record(project_id=project_id, agent_role="Canon", event="org_canon.opted_in", human_reviewer=user.email, detail={"entryId": entry_id})

    # ------------------------------------------------------------------ starter packs, memory
    async def apply_pack(self, project_id: str, user: UserPublic, pack_id: str) -> dict:
        """Add a starter pack's rules to the project; a rule whose title is already there is skipped."""
        from .rule_packs import get_rule_pack

        await self._authz.assert_project_access(project_id, user)
        await self.assert_can_author(project_id, user)
        pack = get_rule_pack(pack_id)
        have = {" ".join(str(r["title"]).lower().split()) for r in await self._db.list_canon(project_id, active_only=False)}
        added, skipped = 0, 0
        for e in pack["entries"]:
            if " ".join(e["title"].lower().split()) in have:
                skipped += 1
                continue
            await self._db.insert_canon(project_id=project_id, category=e["category"], priority=e["priority"], stage=e.get("stage"),
                                        title=e["title"], body=e["body"], user_id=user.id, origin=f"pack:{pack_id}")
            added += 1
        self._audit.record(project_id=project_id, agent_role="Canon", event="canon.pack_applied", human_reviewer=user.email,
                           detail={"pack": pack_id, "added": added, "skipped": skipped})
        return {"pack": pack_id, "added": added, "skipped": skipped}

    async def from_memory(self, project_id: str, memory_id: str, user: UserPublic) -> dict:
        """Promote a remembered decision, convention or lesson to a binding rule."""
        await self._authz.assert_project_access(project_id, user)
        await self.assert_can_author(project_id, user)
        m = await self._db.get_memory(memory_id)
        if not m or (m["scope"] == "project" and m["project_id"] != project_id) or m["scope"] == "user":
            raise SdlcError("NOT_FOUND", "Memory not found")
        category = {"decision": "decision", "convention": "rule", "lesson": "rule"}.get(m["kind"], "preference")
        stage = m["stage"] if isinstance(m.get("stage"), int) and 1 <= m["stage"] <= 6 else None
        row = await self._db.insert_canon(project_id=project_id, category=category, priority="should", stage=stage, title=m["title"][:120],
                                          body=m["body"], user_id=user.id, origin=f"memory:{memory_id}")
        self._audit.record(project_id=project_id, agent_role="Canon", event="canon.from_memory", human_reviewer=user.email,
                           detail={"memoryId": memory_id, "title": m["title"]})
        return self._row(row)

    # ------------------------------------------------------------------ prompt block
    async def active_rules(self, project_id: str, stage_template: int | None = None) -> list[dict]:
        """The rules in force for a stage: the organisation's the project has not opted out of, then the project's own."""
        outs = {o["org_entry_id"] for o in await self._db.list_org_optouts(project_id)}
        def slim(r: Any, scope: str) -> dict:
            return {"id": r.get("id"), "category": r["category"], "priority": r["priority"], "stage": r["stage"], "title": r["title"], "body": r["body"], "scope": scope}

        org = [slim(r, "org") for r in await self._db.list_org_canon(active_only=True) if r.get("id") not in outs]
        own = [slim(r, "project") for r in await self._db.list_canon(project_id, active_only=True)]
        return [e for e in [*org, *own] if e["stage"] is None or stage_template is None or e["stage"] == stage_template]

    async def render_block(self, project_id: str, stage_template: int | None = None) -> str:
        """The Canon block injected into agent prompts. Empty string when no rule applies (prompt stays unchanged)."""
        entries = await self.active_rules(project_id, stage_template)
        if not entries:
            return ""

        lines = [
            "## PROJECT CANON (binding — authored by the project's PM/architects and the organisation)",
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
                org = " _(organisation rule)_" if e.get("scope") == "org" else ""
                lines.append(f"- **[{e['category']}] {e['title']}**{scope}{org}: {e['body']}")
        return "\n".join(lines)

    @staticmethod
    def _row(r: Any) -> dict:
        return {
            "id": r["id"], "category": r["category"], "priority": r["priority"],
            "stage": r["stage"], "title": r["title"], "body": r["body"], "active": r["active"],
            "createdBy": r["created_by"], "origin": r.get("origin") if hasattr(r, "get") else None, "scope": "project",
            "createdAt": r["created_at"].isoformat(), "updatedAt": r["updated_at"].isoformat(),
        }

    @staticmethod
    def _org_row(r: Any) -> dict:
        return {
            "id": r["id"], "category": r["category"], "priority": r["priority"], "stage": r["stage"], "title": r["title"], "body": r["body"],
            "active": r["active"], "createdBy": r["created_by"], "origin": None, "scope": "org",
            "createdAt": r["created_at"].isoformat(), "updatedAt": r["updated_at"].isoformat(),
        }
