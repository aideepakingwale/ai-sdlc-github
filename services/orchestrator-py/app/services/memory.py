"""Memory - what the platform has learned about how this project (and this person) works.

A memory is a short, durable statement: a decision ("we use SQS FIFO"), a convention, a lesson from a
reviewer's change request, or a person's working style. The platform *suggests* them from things people
already do (answering a clarifying question, asking for changes, giving the same instruction twice);
a person confirms. Only active memories reach a prompt, as their own layer, so the agent's context stays
something people have approved.

Scopes: project (default), org (promoted - every project) and user (private; how one person likes to work).
Curating a project's memory (accepting, editing, archiving, promoting) needs the same rights as the Canon:
SUPER_ADMIN, the managing PM, or an SA/TA on the project. Anyone with project access can propose one.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from ..domain.errors import SdlcError
from ..domain.models import UserPublic

KINDS = ("decision", "convention", "lesson", "working_style")
STATUSES = ("suggested", "active", "archived", "rejected")
SCOPES = ("project", "org", "user")
AUTHOR_MEMBERSHIPS = ("SA", "TA")

MAX_PROMPT_ITEMS = 12
MAX_PROMPT_CHARS = 3500
_KIND_ORDER = {"convention": 0, "decision": 1, "lesson": 2, "working_style": 3}
_KIND_LABEL = {"decision": "Decision", "convention": "Convention", "lesson": "Lesson", "working_style": "Working style"}

_CUE = re.compile(r"\b(always|never|prefer|don'?t|do not|avoid|make sure|ensure|instead of|rather than)\b", re.I)
_WORD = re.compile(r"[a-z0-9]{4,}")


def fingerprint(title: str, body: str) -> str:
    norm = " ".join(f"{title} {body}".lower().split())
    return hashlib.sha1(norm.encode()).hexdigest()[:20]


def clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def style_cues(text: str, limit: int = 3) -> list[str]:
    """Sentences that read like a standing preference ("always add a retry section") rather than a one-off ask."""
    out: list[str] = []
    for s in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        s = s.strip(" -*•\t")
        if 20 <= len(s) <= 300 and _CUE.search(s) and s not in out:
            out.append(s)
        if len(out) >= limit:
            break
    return out


def words(text: str) -> set[str]:
    return set(_WORD.findall((text or "").lower()))


class MemoryService:
    def __init__(self, db: Any, authz: Any, audit: Any) -> None:
        self._db = db
        self._authz = authz
        self._audit = audit

    # ------------------------------------------------------------------ authz
    async def can_curate(self, project_id: str, user: UserPublic) -> bool:
        if user.role == "SUPER_ADMIN":
            return True
        project = await self._db.get_project(project_id)
        if not project:
            return False
        if user.role == "PROJECT_MANAGER":
            return project["created_by"] == user.id
        return (await self._authz.get_membership_role(project_id, user.id)) in AUTHOR_MEMBERSHIPS

    async def _assert_can_change(self, mem: dict, user: UserPublic) -> None:
        """Whose call it is to edit, accept or remove this memory."""
        if mem["scope"] == "user":
            if mem.get("owner_id") != user.id:
                raise SdlcError("FORBIDDEN", "This is someone else's working-style memory")
            return
        if user.role == "SUPER_ADMIN":
            return
        if mem["scope"] == "org" or not mem.get("project_id"):
            raise SdlcError("FORBIDDEN", "Only an administrator or the managing PM of the originating project can change an organisation memory")
        await self._authz.assert_project_access(mem["project_id"], user)
        if not await self.can_curate(mem["project_id"], user):
            raise SdlcError("FORBIDDEN", "Managing a project's memory needs the managing PM, a Solution/Technical Architect, or an administrator")

    async def _get(self, project_id: str, memory_id: str, user: UserPublic) -> dict:
        await self._authz.assert_project_access(project_id, user)
        mem = await self._db.get_memory(memory_id)
        visible = mem and (
            (mem["scope"] == "project" and mem["project_id"] == project_id) or mem["scope"] == "org"
            or (mem["scope"] == "user" and mem.get("owner_id") == user.id))
        if not visible:
            raise SdlcError("NOT_FOUND", "Memory not found")
        return mem

    # ------------------------------------------------------------------ read
    async def list(self, project_id: str, user: UserPublic) -> dict[str, Any]:
        await self._authz.assert_project_access(project_id, user)
        rows = await self._db.list_memory(project_id, user.id)
        return {"entries": [self._row(r) for r in rows], "canCurate": await self.can_curate(project_id, user)}

    # ------------------------------------------------------------------ write
    async def create(self, project_id: str, user: UserPublic, payload: dict) -> dict:
        await self._authz.assert_project_access(project_id, user)
        kind = payload.get("kind", "decision")
        scope = payload.get("scope", "project")
        if kind not in KINDS:
            raise SdlcError("VALIDATION_FAILED", f"kind must be one of {list(KINDS)}")
        if scope not in ("project", "user"):
            raise SdlcError("VALIDATION_FAILED", "A new memory is for this project or just for you; promote it to share it wider")
        title, body = clip(payload.get("title") or "", 120), (payload.get("body") or "").strip()
        if len(title) < 3 or not body:
            raise SdlcError("VALIDATION_FAILED", "A memory needs a short title (3+ characters) and the text to remember")
        stage = payload.get("stage")
        if stage is not None and not (isinstance(stage, int) and 1 <= stage <= 7):
            raise SdlcError("VALIDATION_FAILED", "stage must be 1-7 or empty (all stages)")
        curator = scope == "user" or await self.can_curate(project_id, user)
        row = await self._db.insert_memory(
            scope=scope, project_id=project_id, owner_id=user.id if scope == "user" else None, kind=kind, title=title,
            body=body, stage=stage, status="active" if curator else "suggested",
            source={"type": "manual", "by": user.email}, fingerprint=fingerprint(title, body), created_by=user.id,
            reviewed_by=user.id if curator else None)
        if not row:
            raise SdlcError("VALIDATION_FAILED", "That is already in memory")
        self._audit.record(project_id=project_id, agent_role="Memory", event="memory.created", human_reviewer=user.email,
                           detail={"kind": kind, "scope": scope, "status": row["status"], "title": title})
        return self._row(row)

    async def suggest(self, *, project_id: str, kind: str, title: str, body: str, source: dict, stage: int | None = None,
                      scope: str = "project", owner_id: str | None = None, created_by: str | None = None) -> dict | None:
        """The platform proposes a memory. Best-effort and idempotent: a repeat (or one already rejected) is dropped."""
        title, body = clip(title, 120), clip(body, 600)
        if len(title) < 3 or len(body) < 3:
            return None
        row = await self._db.insert_memory(
            scope=scope, project_id=project_id, owner_id=owner_id, kind=kind, title=title, body=body, stage=stage,
            status="suggested", source=source, fingerprint=fingerprint(title, body), created_by=created_by)
        if row:
            self._audit.record(project_id=project_id, agent_role="Memory", event="memory.suggested",
                               detail={"kind": kind, "scope": scope, "source": source.get("type"), "title": title})
        return row

    async def update(self, project_id: str, memory_id: str, user: UserPublic, patch: dict) -> dict:
        mem = await self._get(project_id, memory_id, user)
        await self._assert_can_change(mem, user)
        fields: dict[str, Any] = {}
        for k in ("title", "body"):
            if k in patch:
                v = clip(patch[k], 120) if k == "title" else str(patch[k]).strip()
                if not v:
                    raise SdlcError("VALIDATION_FAILED", f"{k} cannot be empty")
                fields[k] = v
        if "kind" in patch:
            if patch["kind"] not in KINDS:
                raise SdlcError("VALIDATION_FAILED", f"kind must be one of {list(KINDS)}")
            fields["kind"] = patch["kind"]
        if "stage" in patch:
            fields["stage"] = patch["stage"] if isinstance(patch["stage"], int) else None
        if "status" in patch:
            if patch["status"] not in STATUSES:
                raise SdlcError("VALIDATION_FAILED", f"status must be one of {list(STATUSES)}")
            fields["status"] = patch["status"]
            fields["reviewed_by"] = user.id
        if {"title", "body"} & fields.keys():
            fields["fingerprint"] = fingerprint(fields.get("title", mem["title"]), fields.get("body", mem["body"]))
        row = await self._db.update_memory(memory_id, fields)
        self._audit.record(project_id=project_id, agent_role="Memory", event="memory.updated", human_reviewer=user.email,
                           detail={"memoryId": memory_id, "fields": sorted(patch)})
        return self._row(row or mem)

    async def promote(self, project_id: str, memory_id: str, user: UserPublic) -> dict:
        """Share a confirmed project memory with every project in the organisation."""
        mem = await self._get(project_id, memory_id, user)
        if mem["scope"] != "project":
            raise SdlcError("VALIDATION_FAILED", "Only a project memory can be promoted")
        await self._assert_can_change(mem, user)
        if mem["status"] != "active":
            raise SdlcError("VALIDATION_FAILED", "Accept the memory before promoting it")
        row = await self._db.update_memory(memory_id, {"scope": "org"})
        self._audit.record(project_id=project_id, agent_role="Memory", event="memory.promoted", human_reviewer=user.email,
                           detail={"memoryId": memory_id, "title": mem["title"]})
        return self._row(row or mem)

    async def delete(self, project_id: str, memory_id: str, user: UserPublic) -> None:
        mem = await self._get(project_id, memory_id, user)
        await self._assert_can_change(mem, user)
        await self._db.delete_memory(memory_id)
        self._audit.record(project_id=project_id, agent_role="Memory", event="memory.deleted", human_reviewer=user.email,
                           detail={"memoryId": memory_id})

    # ------------------------------------------------------------------ prompt use
    async def select(self, project_id: str, user_id: str, stage_template: int | None, query: str = "") -> list[dict]:
        """The active memories a stage should be given, most relevant first (capped)."""
        rows = [dict(r) for r in await self._db.list_memory(project_id, user_id or "")]
        mine = [r for r in rows if r["status"] == "active"
                and (r["stage"] is None or stage_template is None or r["stage"] == stage_template)]
        q = words(query)

        def rank(r: dict) -> tuple:
            overlap = len(q & words(f"{r['title']} {r['body']}"))
            return (-overlap, _KIND_ORDER.get(r["kind"], 9), -int(r.get("uses") or 0))

        mine.sort(key=rank)
        out, used = [], 0
        for r in mine:
            cost = len(r["title"]) + len(r["body"]) + 12
            if len(out) >= MAX_PROMPT_ITEMS or used + cost > MAX_PROMPT_CHARS:
                break
            out.append(r)
            used += cost
        return out

    @staticmethod
    def render(memories: list[dict]) -> str:
        if not memories:
            return ""
        lines = ["## PROJECT MEMORY (confirmed by your team)",
                 "What this team has decided and learned. Apply it by default; if it conflicts with the Canon or the "
                 "reviewer's instructions in this request, those win."]
        for m in memories:
            who = "you" if m["scope"] == "user" else ("organisation" if m["scope"] == "org" else "project")
            lines.append(f"- **[{_KIND_LABEL.get(m['kind'], m['kind'])} · {who}] {m['title']}**: {m['body']}")
        return "\n".join(lines)

    async def block_for(self, project_id: str, user_id: str, stage_template: int | None, query: str = "",
                        *, record_use: bool = False) -> tuple[str, list[dict]]:
        mems = await self.select(project_id, user_id, stage_template, query)
        if record_use and mems:
            try:
                await self._db.touch_memory([m["id"] for m in mems])
            except Exception:  # noqa: BLE001 - usage counts must never block a run
                pass
        return self.render(mems), mems

    @staticmethod
    def _row(r: Any) -> dict:
        return {
            "id": r["id"], "scope": r["scope"], "projectId": r["project_id"], "kind": r["kind"], "title": r["title"],
            "body": r["body"], "stage": r["stage"], "status": r["status"], "source": r["source"] if isinstance(r["source"], dict) else {},
            "uses": r["uses"], "lastUsedAt": r["last_used_at"].isoformat() if r["last_used_at"] else None,
            "createdBy": r["created_by"], "createdAt": r["created_at"].isoformat(), "updatedAt": r["updated_at"].isoformat(),
        }
