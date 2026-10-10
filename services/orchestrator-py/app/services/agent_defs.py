"""Custom agents and skills: who may see, build and approve them, and how a definition moves from draft to published.

Visibility (the rule the whole feature rests on):
  * **Core** agents and skills are the platform's own files. Only a super-admin sees them, read-only; a project never does.
  * **Organisation** definitions belong to the super-admins. Marked **open**, they are visible to every project, which may copy them.
  * **Project** definitions are private to their project. Another project, and the super-admin's library list, never show them.

Lifecycle of a version:  draft -> (audit) -> pending -> published | rejected.  A published version is immutable; editing it makes the next
draft. Stages pin the version they use, so an approval of v3 never changes a stage that uses v2 until a person switches it.

Who may do what on a project: the managing PM and super-admins always can; others only when the PM grants them "build" and/or "approve".
Nobody approves a version they submitted (a super-admin approving an *organisation* definition they wrote is the one exception: the
library has no one above them, and the audit still has to pass).
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from ..domain.errors import SdlcError
from ..domain.models import UserPublic
from ..repos.pg import new_id
from . import agent_catalog, safe_expr
from .agent_audit import AgentAuditor, blocks, catalogue, warns
from .agent_body import KINDS, MODEL_ROLES, RUNS, ROLE_LABEL, clean_name, core_to_body, default_body, normalise_body, summary
from .agent_runtime import AgentRuntime, Budget, RunResult

log = logging.getLogger("agent_defs")

EDITABLE = ("draft", "rejected")
RUNNABLE = ("published", "superseded")


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(v: Any) -> str | None:
    return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v else None)


class AgentDefService:
    def __init__(self, db: Any, repo: Any, authz: Any, audit: Any, auditor: AgentAuditor, runtime: AgentRuntime, *, canon: Any = None,
                 project_config: Any = None, workflow: Any = None, skill_packs: Callable[[], list[dict[str, Any]]] | None = None) -> None:
        self._db, self._repo, self._authz, self._audit = db, repo, authz, audit
        self._auditor, self._runtime, self._canon, self._config, self._workflow = auditor, runtime, canon, project_config, workflow
        self._skill_packs = skill_packs or (lambda: [])

    # ================================================================== permissions
    @staticmethod
    def _super(user: UserPublic) -> bool:
        return user.role == "SUPER_ADMIN"

    async def rights(self, project_id: str, user: UserPublic) -> dict[str, bool]:
        """What this person may do with custom agents on the project. Raises when they cannot even see the project."""
        await self._authz.assert_project_access(project_id, user)
        project = await self._db.get_project(project_id) or {}
        manage = self._super(user) or (user.role == "PROJECT_MANAGER" and project.get("created_by") == user.id)
        g = await self._repo.get_grant(project_id, user.id)
        return {"view": True, "manage": manage, "edit": manage or bool(g and g["can_edit"]), "approve": manage or bool(g and g["can_approve"])}

    async def _load(self, def_id: str, user: UserPublic, need: str = "view") -> tuple[dict[str, Any], dict[str, bool]]:
        d = await self._repo.get_def(def_id)
        if d is None:
            raise SdlcError("NOT_FOUND", "Agent or skill not found")
        if d["scope"] == "org":
            if self._super(user):
                return d, {"view": True, "edit": True, "approve": True, "manage": True}
            versions = await self._repo.list_versions(def_id)
            if d["open"] and not d["retired"] and any(v["status"] in RUNNABLE for v in versions) and need == "view":
                return d, {"view": True, "edit": False, "approve": False, "manage": False}
            raise SdlcError("NOT_FOUND", "Agent or skill not found")
        r = await self.rights(d["project_id"], user)
        if need == "edit" and not r["edit"]:
            raise SdlcError("FORBIDDEN", "Ask the project manager for permission to build agents and skills on this project")
        if need == "approve" and not r["approve"]:
            raise SdlcError("FORBIDDEN", "Ask the project manager for permission to approve agents and skills on this project")
        return d, r

    def _event(self, d: dict[str, Any] | None, user: UserPublic, event: str, **detail: Any) -> None:
        try:
            self._audit.record(project_id=(d or {}).get("project_id"), agent_role="CustomAgents", event=event, human_reviewer=user.email,
                               detail={"def": (d or {}).get("id"), "name": (d or {}).get("name"), "kind": (d or {}).get("kind"), **detail})
        except Exception:  # noqa: BLE001
            log.warning("could not record %s", event, exc_info=True)

    # ================================================================== shaping
    @staticmethod
    def _def_view(d: dict[str, Any]) -> dict[str, Any]:
        return {"id": d["id"], "kind": d["kind"], "scope": d["scope"], "projectId": d.get("project_id"), "name": d["name"], "open": bool(d["open"]),
                "retired": bool(d["retired"]), "createdBy": d.get("created_by_name") or "", "createdAt": _iso(d.get("created_at")),
                "updatedAt": _iso(d.get("updated_at")),
                "source": ({"kind": d["source_kind"], "id": d.get("source_id"), "name": d.get("source_name"), "version": d.get("source_version")}
                           if d.get("source_kind") else None)}

    @staticmethod
    def _version_view(v: dict[str, Any], *, body: bool = False) -> dict[str, Any]:
        a = v.get("audit") or None
        out = {"version": v["version"], "status": v["status"], "author": v.get("author_name") or "", "submittedBy": v.get("submitted_by_name") or "",
               "submittedById": v.get("submitted_by"), "submittedAt": _iso(v.get("submitted_at")), "decidedBy": v.get("decided_by_name") or "",
               "decidedAt": _iso(v.get("decided_at")), "comment": v.get("decision_comment") or "", "updatedAt": _iso(v.get("updated_at")),
               "audit": ({"ranAt": a.get("ran_at"), "stale": bool(a.get("stale")), "ack": bool(a.get("ack")), "summary": a.get("summary", {})} if a else None)}
        if body:
            out["body"] = v["body"]
            out["auditReport"] = a
        return out

    def _card(self, d: dict[str, Any], versions: list[dict[str, Any]], attachments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        latest = versions[0] if versions else None
        pub = next((v for v in versions if v["status"] == "published"), None)
        shown = pub if (d["scope"] == "org" and not d.get("_owner_view")) else (latest or pub)
        body = (shown or {}).get("body") or {}
        att = attachments or []
        return {**self._def_view(d), "status": (latest or {}).get("status", "draft"), "version": (latest or {}).get("version", 0),
                "publishedVersion": pub["version"] if pub else None, "author": (latest or {}).get("author_name", ""),
                "usedInStages": len({(a["project_id"], a["stage_key"]) for a in att}), "usedInProjects": len({a["project_id"] for a in att}),
                **summary(body), "roleLabel": ROLE_LABEL.get(body.get("role", "generate"), "Generation")}

    # ================================================================== core (read-only)
    def _core_list(self, kind: str) -> list[dict[str, Any]]:
        if kind == "skill":
            return [{"id": p["id"], "kind": "skill", "source": "core", "name": p["name"], "description": p.get("description", ""), "version": int(p.get("version", 1)),
                     "role": "light" if p.get("tier") == "local" else "generate", "roleLabel": ROLE_LABEL["light" if p.get("tier") == "local" else "generate"],
                     "stage": p.get("phase"), "roles": p.get("roles", []), "executor": p.get("executor", ""), "inputs": [], "outputs": []} for p in self._skill_packs()]
        out = []
        for a in agent_catalog.catalog():
            if a["runtime"] == "proposed":
                continue
            role = a["role"] if a["role"] in MODEL_ROLES else "generate"
            out.append({"id": a["id"], "kind": "agent", "source": "core", "name": a["name"], "description": a.get("description", ""), "version": int(a["version"]),
                        "role": role, "roleLabel": ROLE_LABEL.get(role, "Generation"), "stage": a.get("stage"), "category": a.get("category"), "runtime": a["runtime"],
                        "inputs": list(a.get("upstream") or []), "outputs": list(a.get("fields") or a.get("artifacts") or []), "file": a.get("path")})
        return out

    def core_detail(self, user: UserPublic, kind: str, core_id: str) -> dict[str, Any]:
        if not self._super(user):
            raise SdlcError("NOT_FOUND", "Agent or skill not found")
        item = next((c for c in self._core_list(kind) if c["id"] == core_id), None)
        if item is None:
            raise SdlcError("NOT_FOUND", "Agent or skill not found")
        raw = agent_catalog.get(core_id) if kind == "agent" else next((p for p in self._skill_packs() if p["id"] == core_id), None) or {}
        return {**item, "prompt": raw.get("body", ""), "notes": raw.get("notes", ""), "readOnly": True}

    # ================================================================== library and lists
    async def library(self, user: UserPublic, kind: str) -> dict[str, Any]:
        """The super-admin's library: core (locked) and organisation definitions. Project-owned ones are only counted."""
        if not self._super(user):
            raise SdlcError("FORBIDDEN", "Only a super-admin sees the full library")
        if kind not in KINDS:
            raise SdlcError("VALIDATION_FAILED", "kind is agent or skill")
        out = []
        for d in await self._repo.list_defs(scope="org", kind=kind, include_retired=True):
            d["_owner_view"] = True
            out.append(self._card(d, await self._repo.list_versions(d["id"]), await self._repo.attachments_for_def(d["id"])))
        owned = sum((await self._repo.count_defs_by_project()).values())
        return {"core": self._core_list(kind), "org": out, "projectOwned": owned}

    async def for_project(self, user: UserPublic, project_id: str, kind: str | None = None) -> dict[str, Any]:
        r = await self.rights(project_id, user)
        mine = []
        for d in await self._repo.list_defs(project_id=project_id, kind=kind):
            mine.append(self._card({**d, "_owner_view": True}, await self._repo.list_versions(d["id"]), await self._repo.attachments_for_def(d["id"])))
        opened = []
        for d in await self._repo.list_defs(scope="org", kind=kind, open_only=True):
            vs = await self._repo.list_versions(d["id"])
            if any(v["status"] in RUNNABLE for v in vs):
                opened.append(self._card(d, vs, await self._repo.attachments_for_def(d["id"])))
        pending = len(await self._repo.pending_versions(project_id)) if r["approve"] else 0
        return {"rights": r, "mine": mine, "open": opened, "pending": pending}

    async def detail(self, user: UserPublic, def_id: str) -> dict[str, Any]:
        d, r = await self._load(def_id, user)
        versions = await self._repo.list_versions(def_id)
        pub = next((v for v in versions if v["status"] == "published"), None)
        if d["scope"] == "org" and not r["edit"]:
            cur = pub                      # a project looking at an open definition sees the approved version only
            versions = [v for v in versions if v["status"] in RUNNABLE]
        else:
            cur = versions[0] if versions else None
        cases = await self._repo.list_cases(def_id) if r["edit"] else []
        att = await self._repo.attachments_for_def(def_id) if (r["edit"] or r["manage"]) else []
        parent_new = None
        if d.get("source_kind") == "def" and d.get("source_id"):
            src = await self._repo.get_def(d["source_id"])
            if src:
                sv = [v for v in await self._repo.list_versions(src["id"]) if v["status"] == "published"]
                if sv and (d.get("source_version") or 0) < sv[0]["version"]:
                    parent_new = {"name": src["name"], "version": sv[0]["version"]}
        return {"def": self._def_view(d), "rights": r, "current": self._version_view(cur, body=True) if cur else None,
                "published": self._version_view(pub, body=True) if pub else None, "versions": [self._version_view(v) for v in versions],
                "usedIn": sorted({(a["project_id"], a["stage_key"]) for a in att}) if att else [], "cases": [{"id": c["id"], "name": c["name"], "inputs": c["inputs"]} for c in cases],
                "newerSource": parent_new, "canSubmit": bool(cur and cur["status"] in EDITABLE and r["edit"] and self._submittable(cur)),
                "guardrails": catalogue(await self._repo.list_guardrail_overrides())}

    # ================================================================== create, fork, edit
    async def _target(self, user: UserPublic, scope: str, project_id: str | None) -> None:
        if scope == "org":
            if not self._super(user):
                raise SdlcError("FORBIDDEN", "Only a super-admin can add to the organisation library")
        elif scope == "project":
            if not project_id:
                raise SdlcError("VALIDATION_FAILED", "A project is required")
            r = await self.rights(project_id, user)
            if not r["edit"]:
                raise SdlcError("FORBIDDEN", "Ask the project manager for permission to build agents and skills on this project")
        else:
            raise SdlcError("VALIDATION_FAILED", "scope is org or project")

    async def _insert(self, user: UserPublic, kind: str, name: str, scope: str, project_id: str | None, body: dict[str, Any], source: dict[str, Any] | None) -> dict[str, Any]:
        d = await self._repo.insert_def({"id": new_id(), "kind": kind, "scope": scope, "project_id": project_id if scope == "project" else None, "name": name,
                                         "created_by": user.id, "created_by_name": user.displayName or user.email,
                                         **({"source_kind": source["kind"], "source_id": source["id"], "source_name": source["name"], "source_version": source.get("version")} if source else {})})
        await self._repo.insert_version(d["id"], 1, "draft", body, author=user.id, author_name=user.displayName or user.email)
        return d

    async def create(self, user: UserPublic, *, kind: str, name: str, scope: str, project_id: str | None = None, body: dict[str, Any] | None = None) -> dict[str, Any]:
        if kind not in KINDS:
            raise SdlcError("VALIDATION_FAILED", "kind is agent or skill")
        await self._target(user, scope, project_id)
        d = await self._insert(user, kind, clean_name(name), scope, project_id, normalise_body(kind, body) if body else default_body(kind), None)
        self._event(d, user, "custom_agent.created", scope=scope)
        return await self.detail(user, d["id"])

    async def fork(self, user: UserPublic, *, source_kind: str, source_id: str, kind: str, scope: str, project_id: str | None = None, name: str | None = None) -> dict[str, Any]:
        await self._target(user, scope, project_id)
        if source_kind == "core":
            if not self._super(user):
                raise SdlcError("NOT_FOUND", "Agent or skill not found")
            raw = agent_catalog.get(source_id) if kind == "agent" else next((p for p in self._skill_packs() if p["id"] == source_id), None)
            if not raw:
                raise SdlcError("NOT_FOUND", "Agent or skill not found")
            body, src = core_to_body(raw, kind), {"kind": "core", "id": source_id, "name": raw["name"], "version": int(raw.get("version", 1))}
        else:
            s, r = await self._load(source_id, user)
            if s["scope"] == "project" and s["project_id"] != project_id:
                raise SdlcError("NOT_FOUND", "Agent or skill not found")
            versions = await self._repo.list_versions(s["id"])
            pub = next((v for v in versions if v["status"] == "published"), None)
            use = (versions[0] if r["edit"] and versions else pub)
            if use is None:
                raise SdlcError("VALIDATION_FAILED", "There is no approved version to copy yet")
            kind = s["kind"]
            body, src = normalise_body(kind, use["body"]), {"kind": "def", "id": s["id"], "name": s["name"], "version": (pub or use)["version"]}
        d = await self._insert(user, kind, clean_name(name or f"Copy of {src['name']}"), scope, project_id, body, src)
        self._event(d, user, "custom_agent.forked", source=src)
        return await self.detail(user, d["id"])

    async def save_draft(self, user: UserPublic, def_id: str, *, name: str | None, body: dict[str, Any]) -> dict[str, Any]:
        d, _ = await self._load(def_id, user, "edit")
        versions = await self._repo.list_versions(def_id)
        latest = versions[0]
        if latest["status"] == "pending":
            raise SdlcError("GATE_CONFLICT", "It is waiting for approval and cannot be edited. Withdraw it first.")
        new = normalise_body(d["kind"], body)
        changed = new != latest["body"]
        if latest["status"] in EDITABLE:
            if changed:
                aud = latest.get("audit")
                await self._repo.update_version(def_id, latest["version"], body=new, status="draft", audit=({**aud, "stale": True, "ack": False} if aud else None))
        elif changed:
            await self._repo.insert_version(def_id, latest["version"] + 1, "draft", new, author=user.id, author_name=user.displayName or user.email)
        if name is not None and clean_name(name) != d["name"]:
            await self._repo.update_def(def_id, name=clean_name(name))
        if changed:
            self._event(d, user, "custom_agent.edited")
        return await self.detail(user, def_id)

    # ================================================================== audit
    async def _rules(self, project_id: str | None) -> list[str]:
        if not project_id or self._canon is None:
            return []
        try:
            return [f"[{r['priority']}] {r['title']}: {r['body']}" for r in await self._canon.active_rules(project_id)]
        except Exception:  # noqa: BLE001
            return []

    async def context_text(self, project_id: str | None, stage_template: int | None = None) -> str:
        """The project rules and stack, as the agent will be told them."""
        if not project_id:
            return ""
        parts = []
        try:
            if self._canon is not None:
                block = await self._canon.render_block(project_id, stage_template)
                if block:
                    parts.append(block)
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._config is not None:
                from .project_config import render_layers

                decided, _ = render_layers(await self._config.layers(project_id), stage_template)
                if decided:
                    parts.append("## Technology stack decided for this project\n" + decided)
        except Exception:  # noqa: BLE001
            pass
        return "\n\n".join(parts)

    def child_resolver(self, project_id: str | None) -> Callable[[str, "int | None"], Awaitable["dict[str, Any] | None"]]:
        """Finds a delegate: an approved agent of this project, or an open organisation agent. Returns {name, body, version} or None."""
        async def resolve(agent_id: str, version: int | None) -> dict[str, Any] | None:
            d = await self._repo.get_def(agent_id)
            if d is None or d["retired"] or d["kind"] != "agent":
                return None
            if d["scope"] == "project" and d["project_id"] != project_id:
                return None
            if d["scope"] == "org" and not d["open"]:
                return None
            versions = await self._repo.list_versions(agent_id)
            pick = next((v for v in versions if v["version"] == version and v["status"] in RUNNABLE), None) if version else next((v for v in versions if v["status"] == "published"), None)
            return {"name": d["name"], "body": pick["body"], "version": pick["version"]} if pick else None
        return resolve

    async def run_audit(self, user: UserPublic, def_id: str) -> dict[str, Any]:
        d, _ = await self._load(def_id, user, "edit")
        latest = (await self._repo.list_versions(def_id))[0]
        if latest["status"] == "pending":
            raise SdlcError("GATE_CONFLICT", "It is waiting for approval and cannot be audited. Withdraw it first.")
        overrides = await self._repo.list_guardrail_overrides()
        report = await self._auditor.audit(
            kind=d["kind"], name=d["name"], body=latest["body"], rules=await self._rules(d.get("project_id")), project_context=await self.context_text(d.get("project_id")),
            resolve_child=self.child_resolver(d.get("project_id")), overrides=overrides, self_id=d["id"])
        await self._repo.update_version(def_id, latest["version"], audit=report, status="draft" if latest["status"] == "rejected" else latest["status"])
        self._event(d, user, "custom_agent.audited", version=latest["version"], summary=report["summary"], tokens=report["tokens"])
        return report

    async def apply_fix(self, user: UserPublic, def_id: str, finding_id: str) -> dict[str, Any]:
        d, _ = await self._load(def_id, user, "edit")
        latest = (await self._repo.list_versions(def_id))[0]
        if latest["status"] not in EDITABLE:
            raise SdlcError("GATE_CONFLICT", "Only a draft can be changed")
        rep = latest.get("audit") or {}
        f = next((x for x in rep.get("findings", []) if x["id"] == finding_id), None)
        fix = (f or {}).get("fix")
        if not f or not fix:
            raise SdlcError("NOT_FOUND", "That finding has no suggested fix")
        prompt = latest["body"].get("prompt", "")
        if fix["find"] not in prompt:
            raise SdlcError("GATE_CONFLICT", "The prompt has changed since the audit. Run it again.")
        body = {**latest["body"], "prompt": re.sub(r"\n{3,}", "\n\n", prompt.replace(fix["find"], fix["replace"], 1)).strip()}
        body = normalise_body(d["kind"], body)
        overrides = await self._repo.list_guardrail_overrides()
        fresh = self._auditor.refresh(rep, kind=d["kind"], name=d["name"], body=body, overrides=overrides, fixed_snippet=f.get("snippet", ""))
        await self._repo.update_version(def_id, latest["version"], body=body, audit=fresh, status="draft")
        self._event(d, user, "custom_agent.fix_applied", finding=f["title"])
        return await self.detail(user, def_id)

    async def acknowledge(self, user: UserPublic, def_id: str, ack: bool) -> dict[str, Any]:
        d, _ = await self._load(def_id, user, "edit")
        latest = (await self._repo.list_versions(def_id))[0]
        if not latest.get("audit"):
            raise SdlcError("GATE_CONFLICT", "Run the audit first")
        await self._repo.update_version(def_id, latest["version"], audit={**latest["audit"], "ack": bool(ack), "ack_by": user.email if ack else None})
        return await self.detail(user, def_id)

    # ================================================================== lifecycle
    @staticmethod
    def _submittable(v: dict[str, Any]) -> bool:
        a = v.get("audit")
        return bool(a) and not a.get("stale") and blocks(a) == 0 and (warns(a) == 0 or bool(a.get("ack")))

    async def submit(self, user: UserPublic, def_id: str) -> dict[str, Any]:
        d, _ = await self._load(def_id, user, "edit")
        latest = (await self._repo.list_versions(def_id))[0]
        if latest["status"] not in EDITABLE:
            raise SdlcError("GATE_CONFLICT", "Only a draft can be submitted")
        a = latest.get("audit")
        if not a:
            raise SdlcError("GATE_CONFLICT", "Run the audit before submitting")
        if a.get("stale"):
            raise SdlcError("GATE_CONFLICT", "It changed since the last audit. Run the audit again.")
        if blocks(a):
            raise SdlcError("GUARDRAIL_BLOCKED", f"The audit found {blocks(a)} blocking problem(s). Fix them first.")
        if warns(a) and not a.get("ack"):
            raise SdlcError("GATE_CONFLICT", "Read the audit warnings and accept them before submitting")
        await self._repo.update_version(def_id, latest["version"], status="pending", submitted_by=user.id, submitted_by_name=user.displayName or user.email,
                                        submitted_at=_now(), decided_by=None, decided_by_name=None, decided_at=None, decision_comment=None)
        self._event(d, user, "custom_agent.submitted", version=latest["version"])
        return await self.detail(user, def_id)

    async def withdraw(self, user: UserPublic, def_id: str) -> dict[str, Any]:
        d, _ = await self._load(def_id, user, "edit")
        latest = (await self._repo.list_versions(def_id))[0]
        if latest["status"] != "pending":
            raise SdlcError("GATE_CONFLICT", "It is not waiting for approval")
        await self._repo.update_version(def_id, latest["version"], status="draft")
        self._event(d, user, "custom_agent.withdrawn", version=latest["version"])
        return await self.detail(user, def_id)

    async def decide(self, user: UserPublic, def_id: str, decision: str, comment: str = "") -> dict[str, Any]:
        if decision not in ("approve", "changes", "reject"):
            raise SdlcError("VALIDATION_FAILED", "decision is approve, changes or reject")
        d, _ = await self._load(def_id, user, "approve")
        latest = (await self._repo.list_versions(def_id))[0]
        if latest["status"] != "pending":
            raise SdlcError("GATE_CONFLICT", "It is not waiting for approval")
        if d["scope"] == "project" and latest.get("submitted_by") == user.id:
            raise SdlcError("FORBIDDEN", "Someone else has to approve what you submitted")
        c = (comment or "").strip()
        if decision != "approve" and len(c) < 3:
            raise SdlcError("VALIDATION_FAILED", "Say what needs to change")
        who = {"decided_by": user.id, "decided_by_name": user.displayName or user.email, "decided_at": _now(), "decision_comment": c or None}
        if decision == "approve":
            for v in await self._repo.list_versions(def_id):
                if v["status"] == "published":
                    await self._repo.update_version(def_id, v["version"], status="superseded")
            await self._repo.update_version(def_id, latest["version"], status="published", **who)
        else:
            await self._repo.update_version(def_id, latest["version"], status="rejected", **who)
        self._event(d, user, f"custom_agent.{'approved' if decision == 'approve' else 'rejected'}", version=latest["version"], decision=decision, comment=c)
        return await self.detail(user, def_id)

    async def pending(self, user: UserPublic, project_id: str | None) -> list[dict[str, Any]]:
        if project_id:
            if not (await self.rights(project_id, user))["approve"]:
                raise SdlcError("FORBIDDEN", "Ask the project manager for permission to approve agents and skills on this project")
        elif not self._super(user):
            raise SdlcError("FORBIDDEN", "Only a super-admin approves organisation agents")
        out = []
        for v in await self._repo.pending_versions(project_id):
            prev = next((x for x in await self._repo.list_versions(v["def_id"]) if x["status"] in ("published", "superseded") and x["version"] < v["version"]), None)
            out.append({"defId": v["def_id"], "name": v["name"], "kind": v["kind"], "scope": v["scope"], "version": v["version"], "submittedBy": v.get("submitted_by_name") or "",
                        "submittedById": v.get("submitted_by"), "submittedAt": _iso(v.get("submitted_at")), "audit": (v.get("audit") or {}).get("summary", {}),
                        "acknowledged": bool((v.get("audit") or {}).get("ack")), "source": v.get("source_name"),
                        "previousPrompt": (prev or {}).get("body", {}).get("prompt", "")})
        return out

    async def set_open(self, user: UserPublic, def_id: str, open_: bool) -> dict[str, Any]:
        d, _ = await self._load(def_id, user, "edit")
        if d["scope"] != "org":
            raise SdlcError("VALIDATION_FAILED", "Only organisation agents and skills can be opened to projects")
        if open_ and not any(v["status"] == "published" for v in await self._repo.list_versions(def_id)):
            raise SdlcError("GATE_CONFLICT", "Approve a version before opening it to projects")
        await self._repo.update_def(def_id, open=bool(open_))
        self._event(d, user, "custom_agent.opened" if open_ else "custom_agent.closed")
        return await self.detail(user, def_id)

    async def retire(self, user: UserPublic, def_id: str) -> dict[str, Any]:
        d, _ = await self._load(def_id, user, "edit")
        await self._repo.update_def(def_id, retired=True, open=False)
        await self._repo.drop_attachments_for_def(def_id, d.get("project_id") if d["scope"] == "project" else None)
        self._event(d, user, "custom_agent.retired")
        return {"retired": True}

    async def delete_unpublished(self, user: UserPublic, def_id: str) -> dict[str, Any]:
        d, _ = await self._load(def_id, user, "edit")
        if any(v["status"] in ("published", "superseded") for v in await self._repo.list_versions(def_id)):
            raise SdlcError("GATE_CONFLICT", "It has an approved version, so it can only be retired")
        await self._repo.delete_def(def_id)
        self._event(d, user, "custom_agent.deleted")
        return {"deleted": True}

    # ================================================================== stage attachments
    async def _stage_keys(self, project_id: str) -> dict[str, dict[str, Any]]:
        if self._workflow is None:
            return {}
        view = await self._workflow.view(project_id)
        return {s["key"]: s for s in view["stages"]}

    async def _visible_to_project(self, d: dict[str, Any] | None, project_id: str) -> bool:
        return bool(d and not d["retired"] and ((d["scope"] == "project" and d["project_id"] == project_id) or (d["scope"] == "org" and d["open"])))

    async def stage_items(self, user: UserPublic, project_id: str, stage_key: str) -> dict[str, Any]:
        r = await self.rights(project_id, user)
        stages = await self._stage_keys(project_id)
        if stages and stage_key not in stages:
            raise SdlcError("NOT_FOUND", "That stage does not exist in this project's workflow")
        att = await self._repo.list_attachments(project_id, stage_key)
        items, taken = [], set()
        for a in att:
            d = await self._repo.get_def(a["def_id"])
            if not await self._visible_to_project(d, project_id):
                continue
            vs = await self._repo.list_versions(d["id"])
            pub = next((v for v in vs if v["status"] == "published"), None)
            pinned = next((v for v in vs if v["version"] == a["pinned_version"]), None)
            body = (pinned or pub or {}).get("body") or {}
            taken.add(d["id"])
            items.append({"defId": d["id"], "name": d["name"], "kind": d["kind"], "source": d["scope"], "pinnedVersion": a["pinned_version"], "publishedVersion": pub["version"] if pub else None,
                          "newerAvailable": bool(pub and pub["version"] > a["pinned_version"]), "runs": a["runs"], "condition": a["condition"], "roles": list(a["roles"] or []),
                          **summary(body), "roleLabel": ROLE_LABEL.get(body.get("role", "generate"), "Generation"), "outputsDetail": body.get("outputs", []),
                          "childrenDetail": [c["agent_id"] for c in body.get("children", [])]})
        available = []
        for d in [*await self._repo.list_defs(project_id=project_id), *await self._repo.list_defs(scope="org", open_only=True)]:
            if d["id"] in taken:
                continue
            vs = await self._repo.list_versions(d["id"])
            pub = next((v for v in vs if v["status"] == "published"), None)
            if pub:
                available.append({"defId": d["id"], "name": d["name"], "kind": d["kind"], "source": d["scope"], "version": pub["version"], **summary(pub["body"])})
        return {"rights": r, "items": items, "available": available, "stage": stage_key}

    async def set_stage_items(self, user: UserPublic, project_id: str, stage_key: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        r = await self.rights(project_id, user)
        if not r["edit"]:
            raise SdlcError("FORBIDDEN", "Ask the project manager for permission to change which agents a stage uses")
        stages = await self._stage_keys(project_id)
        if stages and stage_key not in stages:
            raise SdlcError("NOT_FOUND", "That stage does not exist in this project's workflow")
        clean, seen = [], set()
        for it in items:
            did = str(it.get("defId") or "")
            if did in seen:
                continue
            seen.add(did)
            d = await self._repo.get_def(did)
            if not await self._visible_to_project(d, project_id):
                raise SdlcError("VALIDATION_FAILED", "One of those agents or skills is not available to this project")
            vs = await self._repo.list_versions(did)
            pin = int(it.get("pinnedVersion") or 0) or next((v["version"] for v in vs if v["status"] == "published"), 0)
            ver = next((v for v in vs if v["version"] == pin and v["status"] in RUNNABLE), None)
            if ver is None:
                raise SdlcError("VALIDATION_FAILED", f"'{d['name']}' has no approved version {pin or ''}".strip())
            runs = it.get("runs") or "always"
            if runs not in RUNS or (d["kind"] == "skill" and runs != "on_request"):
                runs = "on_request" if d["kind"] == "skill" else "always"
            cond = str(it.get("condition") or "").strip()
            if runs == "when":
                probs = safe_expr.check(cond, {i["name"] for i in ver["body"].get("inputs", [])} | {"brief"})
                if not cond or probs:
                    raise SdlcError("VALIDATION_FAILED", f"The condition for '{d['name']}': {probs[0] if probs else 'say when it should run'}")
            roles = [x for x in (it.get("roles") or []) if isinstance(x, str)]
            clean.append({"def_id": did, "pinned_version": pin, "runs": runs, "condition": cond if runs == "when" else "", "roles": roles})
        await self._repo.replace_attachments(project_id, stage_key, clean, user.id)
        self._event(None, user, "custom_agent.stage_set", stage=stage_key, items=[c["def_id"] for c in clean], project=project_id)
        return await self.stage_items(user, project_id, stage_key)

    async def resolve_for_run(self, project_id: str, stage_key: str, *, kind: str = "agent") -> list[dict[str, Any]]:
        """The approved, pinned agents (or skills) a stage runs, with everything they delegate to, ready to run. Retired ones are left out."""
        out = []
        resolver = self.child_resolver(project_id)
        for a in await self._repo.list_attachments(project_id, stage_key):
            d = await self._repo.get_def(a["def_id"])
            if not await self._visible_to_project(d, project_id) or d["kind"] != kind:
                continue
            ver = next((v for v in await self._repo.list_versions(d["id"]) if v["version"] == a["pinned_version"] and v["status"] in RUNNABLE), None)
            if ver is None:
                continue
            resolved: dict[str, Any] = {}

            async def gather(body: dict[str, Any], depth: int) -> None:
                for c in body.get("children") or []:
                    if c["agent_id"] in resolved or depth > 2:
                        continue
                    ch = await resolver(c["agent_id"], c.get("version"))
                    if ch:
                        resolved[c["agent_id"]] = ch
                        await gather(ch["body"], depth + 1)

            await gather(ver["body"], 1)
            out.append({"def_id": d["id"], "name": d["name"], "version": ver["version"], "kind": d["kind"], "body": ver["body"], "runs": a["runs"],
                        "condition": a["condition"], "roles": list(a["roles"] or []) or list(ver["body"].get("roles") or []), "resolved": resolved})
        return out

    async def skills_for_stage(self, project_id: str, stage_key: str) -> list[dict[str, Any]]:
        return await self.resolve_for_run(project_id, stage_key, kind="skill")

    # ================================================================== people
    async def grants(self, user: UserPublic, project_id: str) -> dict[str, Any]:
        r = await self.rights(project_id, user)
        rows = await self._repo.list_grants(project_id)
        return {"rights": r, "grants": [{"userId": g["user_id"], "email": g.get("email") or "", "name": g.get("display_name") or g.get("email") or g["user_id"],
                                         "canEdit": bool(g["can_edit"]), "canApprove": bool(g["can_approve"])} for g in rows]}

    async def set_grant(self, user: UserPublic, project_id: str, target_user_id: str, can_edit: bool, can_approve: bool) -> dict[str, Any]:
        if not (await self.rights(project_id, user))["manage"]:
            raise SdlcError("FORBIDDEN", "Only the managing project manager or a super-admin can give these permissions")
        if not await self._authz.get_membership_role(project_id, target_user_id):
            raise SdlcError("VALIDATION_FAILED", "That person is not on this project's team")
        await self._repo.set_grant(project_id, target_user_id, bool(can_edit or can_approve), bool(can_approve), user.id)
        self._event(None, user, "custom_agent.grant", project=project_id, target=target_user_id, canEdit=can_edit, canApprove=can_approve)
        return await self.grants(user, project_id)

    # ================================================================== guardrails and cases
    async def guardrails(self) -> list[dict[str, str]]:
        return catalogue(await self._repo.list_guardrail_overrides())

    async def set_guardrail(self, user: UserPublic, gid: str, severity: str) -> list[dict[str, str]]:
        if not self._super(user):
            raise SdlcError("FORBIDDEN", "Only a super-admin changes the guardrails")
        if gid not in {g["id"] for g in catalogue()} or severity not in ("block", "warn"):
            raise SdlcError("VALIDATION_FAILED", "Unknown guardrail or severity")
        await self._repo.set_guardrail(gid, severity, user.id)
        self._event(None, user, "custom_agent.guardrail", guardrail=gid, severity=severity)
        return await self.guardrails()

    async def add_case(self, user: UserPublic, def_id: str, name: str, inputs: dict[str, Any]) -> dict[str, Any]:
        await self._load(def_id, user, "edit")
        c = await self._repo.insert_case(new_id(), def_id, clean_name(name), inputs or {}, user.id)
        return {"id": c["id"], "name": c["name"], "inputs": c["inputs"]}

    async def delete_case(self, user: UserPublic, def_id: str, case_id: str) -> dict[str, Any]:
        await self._load(def_id, user, "edit")
        await self._repo.delete_case(def_id, case_id)
        return {"deleted": True}

    # ================================================================== running
    async def run_test(self, user: UserPublic, def_id: str, inputs: dict[str, Any], version: int | None = None) -> dict[str, Any]:
        d, r = await self._load(def_id, user, "edit")
        versions = await self._repo.list_versions(def_id)
        v = next((x for x in versions if x["version"] == version), None) if version else versions[0]
        if v is None:
            raise SdlcError("NOT_FOUND", "Version not found")
        res = await self._runtime.run(agent_id=d["id"], name=d["name"], body=v["body"], inputs=inputs or {}, project_context=await self.context_text(d.get("project_id")),
                                      resolve_child=self.child_resolver(d.get("project_id")), budget=Budget(), tag="custom_agent_test")
        self._event(d, user, "custom_agent.test_run", version=v["version"], tokens=res.total_tokens())
        return self.result_view(res, show_prompts=True)

    @staticmethod
    def result_view(res: RunResult, *, show_prompts: bool) -> dict[str, Any]:
        return {"outputs": res.outputs, "tokens": res.usage, "totalTokens": res.total_tokens(), "provider": res.provider, "model": res.model, "mock": res.mock,
                "warnings": res.warnings, "tree": res.tree(), "context": res.context,
                **({"systemPrompt": res.system_prompt, "userPrompt": res.user_prompt} if show_prompts else {})}

    async def run_skill(self, project_id: str, def_id: str, user: UserPublic, text: str) -> dict[str, Any]:
        """A person runs a custom skill from a stage. The skill's roles and stage attachment were checked by the caller."""
        d = await self._repo.get_def(def_id)
        if not await self._visible_to_project(d, project_id) or d["kind"] != "skill":
            raise SdlcError("NOT_FOUND", "Skill not found")
        ver = next((v for v in await self._repo.list_versions(def_id) if v["status"] == "published"), None)
        if ver is None:
            raise SdlcError("NOT_FOUND", "Skill has no approved version")
        return await self.run_skill_body(project_id, d, ver, text)

    async def run_skill_body(self, project_id: str, d: dict[str, Any], ver: dict[str, Any], text: str) -> dict[str, Any]:
        body = ver["body"]
        first = (body.get("inputs") or [{"name": "input"}])[0]["name"]
        res = await self._runtime.run(agent_id=d["id"], name=d["name"], body=body, inputs={first: text}, project_context=await self.context_text(project_id), tag="custom_skill")
        out = body["outputs"][0]["name"] if body.get("outputs") else next(iter(res.outputs), "result")
        value = res.outputs.get(out, "")
        return {"output": value if isinstance(value, str) else __import__("json").dumps(value, ensure_ascii=False, indent=2), "meta": {"provider": res.provider, "model": res.model, "tokens": res.total_tokens(), "version": ver["version"], "custom": True}}
