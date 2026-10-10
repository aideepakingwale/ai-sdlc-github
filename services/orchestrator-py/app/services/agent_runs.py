"""Running a custom agent or skill when a person asks: on its own, or inside a stage.

* **On its own** (no stage): the approved version runs with inputs the person types or the project supplies (an approved artefact, the project
  rules or stack). The answer is kept in the run history and shown to the person; nothing in the project's stages changes.
* **Inside a stage** (on request): the agent attached to that stage runs at the version the stage pinned, and what it writes becomes artefacts of
  that stage, with a "How this was made" record. Needs write access to the stage, and a stage that has run and is not yet approved.

Both count against the agent's token cap and the project's monthly budget.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ..domain.errors import SdlcError
from ..domain.models import ArtifactRef, ContextArtifact, UserPublic
from ..repos.pg import new_id
from .agent_runtime import render_output
from .content_store import artifact_key
from .guardrails import enforce_input, sanitise_output

log = logging.getLogger("agent_runs")

SAVEABLE = ("PENDING_REVIEW", "AMEND_REQUESTED", "ESCALATED")      # a stage that has produced work and is not approved
PREFILL_CHARS = 20_000
KEEP_INPUT = 2_000


def _clip(v: Any, n: int) -> Any:
    if isinstance(v, str):
        return v if len(v) <= n else v[:n] + f"… ({len(v) - n} more characters)"
    return v


class AgentRunService:
    def __init__(self, defs: Any, repo: Any, db: Any, dynamo: Any, workflow: Any, chat: Any, deps: Any, audit: Any) -> None:
        self._defs, self._repo, self._db, self._dynamo, self._workflow, self._chat, self._deps, self._audit = defs, repo, db, dynamo, workflow, chat, deps, audit

    # ------------------------------------------------------------------ the form
    async def _artefact_text(self, project_id: str, type_: str) -> tuple[str, str] | None:
        rows = await self._db.list_artefacts(project_id)
        row = next((r for r in rows if str(r["type"]).upper() == type_.upper()), None)
        if row is None:
            return None
        body = None
        if row["storage_key"]:
            try:
                body = await self._deps.content.get(row["storage_key"])
            except Exception:  # noqa: BLE001
                body = None
        return (body or row["content"] or "")[:PREFILL_CHARS], f"{row['title']} (stage {row['phase']})"

    async def _prefill(self, project_id: str, body: dict[str, Any]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for i in body.get("inputs", []):
            src, value, origin = i["source"], None, ""
            if src.startswith("upstream:"):
                got = await self._artefact_text(project_id, src.split(":", 1)[1])
                if got:
                    value, origin = got
            elif src == "context:rules":
                value, origin = await self._defs.rules_block(project_id), "the project rules"
            elif src == "context:stack":
                value, origin = await self._defs.stack_block(project_id), "the stack decision"
            if value:
                out[i["name"]] = {"value": value, "from": origin}
        return out

    async def _stage(self, project_id: str, stage_key: str) -> dict[str, Any]:
        view = await self._workflow.view(project_id)
        st = next((s for s in view["stages"] if s["key"] == stage_key), None)
        if st is None:
            raise SdlcError("NOT_FOUND", "That stage does not exist in this project's workflow")
        return st

    async def form(self, user: UserPublic, project_id: str, def_id: str, stage_key: str | None = None) -> dict[str, Any]:
        r = await self._defs.rights(project_id, user)
        d, ver, att = await self._defs.runnable(project_id, def_id, stage_key)
        body = ver["body"]
        pre = await self._prefill(project_id, body)
        can_save, why = False, ""
        if stage_key:
            can_save, why = await self._can_save(user, project_id, await self._stage(project_id, stage_key))
        return {"rights": r, "def": {"id": d["id"], "name": d["name"], "kind": d["kind"], "version": ver["version"], "description": body.get("description", ""), "scope": d["scope"]},
                "stage": stage_key, "canRun": can_save or not stage_key, "why": why,
                "inputs": [{"name": i["name"], "type": i["type"], "required": bool(i.get("required", True)), "description": i.get("description", ""), "source": i["source"],
                            "prefill": pre.get(i["name"], {}).get("value"), "prefillFrom": pre.get(i["name"], {}).get("from")} for i in body.get("inputs", [])],
                "outputs": [{"name": o["name"], "type": o["type"], "artefactType": o["artefact_type"], "format": o["format"]} for o in body.get("outputs", [])],
                "savesToStage": bool(stage_key)}

    async def _can_save(self, user: UserPublic, project_id: str, stage: dict[str, Any]) -> tuple[bool, str]:
        if not await self._chat.can_write_stage(project_id, int(stage["seq"]), user):
            return False, "You do not have write access to this stage"
        state = await self._dynamo.get_phase_state(project_id, int(stage["seq"]))
        status = (state or {}).get("status", "NOT_STARTED")
        if status == "APPROVED":
            return False, "This stage is approved. Ask a reviewer to reopen it before adding to it"
        if status not in SAVEABLE:
            return False, "Run the stage first; agents add to a stage's work once it has produced something"
        return True, ""

    # ------------------------------------------------------------------ the run
    async def run(self, user: UserPublic, project_id: str, def_id: str, inputs: dict[str, Any], stage_key: str | None = None) -> dict[str, Any]:
        await self._defs.rights(project_id, user)
        d, ver, att = await self._defs.runnable(project_id, def_id, stage_key)
        body = ver["body"]
        stage = None
        if stage_key:
            stage = await self._stage(project_id, stage_key)
            ok, why = await self._can_save(user, project_id, stage)
            if not ok:
                raise SdlcError("FORBIDDEN" if "access" in why else "GATE_CONFLICT", why)
        elif d["kind"] == "skill" and body.get("roles") and user.role not in ("SUPER_ADMIN", "PROJECT_MANAGER"):
            if await self._defs._authz.get_membership_role(project_id, user.id) not in body["roles"]:
                raise SdlcError("FORBIDDEN", f"'{d['name']}' is for {', '.join(body['roles'])}")
        pre = await self._prefill(project_id, body)
        vals: dict[str, Any] = {}
        missing: list[str] = []
        for i in body.get("inputs", []):
            given = (inputs or {}).get(i["name"])
            if given in (None, ""):
                if i["name"] in pre:
                    vals[i["name"]] = pre[i["name"]]["value"]
                elif i.get("required", True):
                    missing.append(i["name"])
                continue
            if isinstance(given, str):
                enforce_input(given, channel="skill")          # what a person types is screened like a chat message
            vals[i["name"]] = given
        if missing:
            raise SdlcError("VALIDATION_FAILED", f"It needs {', '.join(missing)}. Type it in; nothing in the project supplies it")
        res = await self._defs._runtime.run(
            agent_id=d["id"], name=d["name"], body=body, inputs=vals, project_context=await self._defs.context_text(project_id, stage["template"] if stage else None),
            resolve_child=self._defs.child_resolver(project_id), tag="custom_agent_request" if stage else "custom_agent_standalone", project_id=project_id,
            source="stage_request" if stage else "standalone")
        saved: list[dict[str, str]] = []
        if stage:
            for o in body.get("outputs", []):
                content = render_output(res.outputs.get(o["name"]), o["format"])
                title = d["name"] if len(body["outputs"]) == 1 else f"{d['name']} - {o['name']}"
                saved.append(await self._save(project_id, int(stage["seq"]), o["artefact_type"], title, content, res.record()))
        run_id = new_id()
        await self._repo.insert_run({
            "id": run_id, "def_id": d["id"], "version": ver["version"], "project_id": project_id, "stage_key": stage_key, "user_id": user.id, "user_name": user.displayName or user.email,
            "inputs": {k: _clip(v, KEEP_INPUT) for k, v in vals.items()}, "outputs": res.outputs, "warnings": res.warnings, "saved": saved, "tokens": res.total_tokens(),
            "provider": res.provider, "model": res.model})
        self._event(project_id, user, stage, "custom_agent.run_on_request" if stage else "custom_agent.run_standalone", d, ver["version"], res, saved)
        return {"runId": run_id, "def": {"id": d["id"], "name": d["name"], "kind": d["kind"], "version": ver["version"]}, "outputs": res.outputs, "saved": saved,
                "warnings": res.warnings, "tokens": res.total_tokens(), "provider": res.provider, "model": res.model, "mock": res.mock, "tree": res.tree(),
                "context": res.context, "stage": stage_key,
                "outputsDetail": [{"name": o["name"], "type": o["type"], "artefactType": o["artefact_type"], "format": o["format"]} for o in body.get("outputs", [])]}

    async def _save(self, project_id: str, seq: int, type_: str, title: str, content: str, run: dict[str, Any]) -> dict[str, str]:
        """The same persistence a stage run uses: masked, stored (content store, or the database if that fails), versioned, indexed, with its run record."""
        deps = self._deps
        content, masked = sanitise_output(content)
        if masked:
            deps.audit.record(project_id=project_id, phase=seq, agent_role="OutputGuardrail", event="guardrail.artifact_masked", detail={"rules": masked, "type": type_, "title": title})
        artefact_id = new_id()
        key = artifact_key(project_id, seq, type_, artefact_id)
        stored = True
        try:
            await deps.content.put(key, content)
        except Exception as err:  # noqa: BLE001
            stored = False
            log.warning("content-store put failed for %s '%s' (%s); keeping the body in the database", type_, title, err)
        prev = await deps.db.latest_artefact_version(project_id, seq, type_, title)
        await deps.db.insert_artefact(
            project_id=project_id, phase=seq, type_=type_, title=title, content=content, url=None, storage_key=(key if stored else None),
            storage_mode=(deps.content.mode if stored else "db"), artefact_id=artefact_id, lineage_id=(prev["lineage_id"] if prev else artefact_id),
            version=((prev["version"] + 1) if prev else 1))
        try:
            await deps.rag.index_artifact(project_id, artefact_id, ContextArtifact(phase=seq, type=type_, title=title, summary=content[:300], exact=False, content=None,
                                                                                    ref=ArtifactRef(url=None, key=(key if stored else None))))
        except Exception:  # noqa: BLE001 - the artefact is saved; it just is not searchable yet
            log.warning("could not index %s '%s'", type_, title, exc_info=True)
        try:
            await deps.db.insert_artefact_run(artefact_id=artefact_id, project_id=project_id, phase=seq, field=None, run=run)
        except Exception:  # noqa: BLE001
            log.warning("could not record the run for %s", type_, exc_info=True)
        return {"id": artefact_id, "type": type_, "title": title}

    def _event(self, project_id: str, user: UserPublic, stage: dict[str, Any] | None, event: str, d: dict[str, Any], version: int, res: Any, saved: list[dict[str, str]]) -> None:
        try:
            self._audit.record(project_id=project_id, phase=int(stage["seq"]) if stage else 0, agent_role=d["name"], event=event, human_reviewer=user.email, provider=res.provider,
                               model=res.model, prompt_tokens=res.usage["promptTokens"], completion_tokens=res.usage["completionTokens"],
                               detail={"def": d["id"], "version": version, "stage": stage["key"] if stage else None, "saved": [s["id"] for s in saved]})
        except Exception:  # noqa: BLE001
            log.warning("could not audit a custom agent run", exc_info=True)

    # ------------------------------------------------------------------ history
    async def history(self, user: UserPublic, project_id: str, def_id: str | None = None, limit: int = 20) -> dict[str, Any]:
        await self._defs.rights(project_id, user)
        rows = await self._repo.list_runs(project_id, def_id, max(1, min(50, limit)))
        names: dict[str, str] = {}
        out = []
        for r in rows:
            if r["def_id"] not in names:
                d = await self._repo.get_def(r["def_id"])
                names[r["def_id"]] = d["name"] if d else "(deleted)"
            out.append({"id": r["id"], "defId": r["def_id"], "name": names[r["def_id"]], "version": r["version"], "stage": r["stage_key"], "by": r["user_name"] or "",
                        "at": r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else str(r["created_at"]), "tokens": r["tokens"], "inputs": r["inputs"],
                        "outputs": r["outputs"], "warnings": r["warnings"], "saved": r["saved"], "model": r["model"] or ""})
        return {"runs": out}
