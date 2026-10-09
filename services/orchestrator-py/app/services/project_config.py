"""projectconfig.json: the project's own configuration, empty at creation and filled in over time.

Today it holds the technology stack, described by layer (frontend, backend, database, cache, messaging, hosting, ...) instead of one
string. Values arrive two ways: the platform identifies them from the project's documents (stack advisor, after stages 1, 2 and 3) or
from an uploaded codebase, and authorised people set them on the Project Context screen. A person's value is `pinned` and is never
changed by the platform; `identified` values may be replaced by a later stage; `open` marks a layer left for the Technical Architect.

The `project_config` table is the source of truth. Every write is mirrored to the content store as a real file (projectconfig.json) and
`projects.tech_stack` keeps the one-line summary (backend and database) for everything that reads a single string.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from ..domain.errors import SdlcError
from ..domain.models import UserPublic
from .tech_catalog import get_layers, layer_ids, layer_label

log = logging.getLogger("project_config")

SCHEMA_VERSION = 1
STATUSES = ("pinned", "identified", "open")
SOURCES = ("user", "llm", "detected", "ta", "legacy", "preset")
CONFIDENCES = ("high", "medium", "low")
MAX_ENTRIES = 40

# The layers each stage needs. Stage 3 (technical design) decides and sees everything; custom stages see everything.
STAGE_LAYERS: dict[int, tuple[str, ...]] = {
    1: ("hosting", "identity"),
    2: ("hosting", "compute", "frontend", "backend", "database", "cache", "messaging", "search", "identity"),
    4: ("frontend", "backend", "database", "cache", "messaging", "search", "identity", "cicd"),
    5: ("hosting", "compute", "iac", "cicd", "observability", "messaging", "database", "cache", "search", "identity"),
    6: ("frontend", "backend", "database", "cache", "messaging", "search", "identity"),
}


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def empty_config() -> dict[str, Any]:
    return {"schemaVersion": SCHEMA_VERSION, "version": 1, "updatedAt": now_iso(), "stack": {"summary": "", "layers": [], "conflicts": [], "advisor": {}}}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40]


def entry_id(layer: str, component: str = "") -> str:
    return f"{layer}/{slug(component)}" if slug(component) else layer


def _clip(v: Any, n: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


def normalise_entry(raw: dict[str, Any], *, status: str, source: str, actor: str = "", stage: int | None = None) -> dict[str, Any]:
    """A validated entry. Raises VALIDATION_FAILED for an unknown layer, or for a missing technology unless the layer is left open."""
    layer = str(raw.get("layer") or "").strip().lower()
    if layer not in layer_ids():
        raise SdlcError("VALIDATION_FAILED", f"Unknown stack layer '{raw.get('layer')}'")
    if status not in STATUSES or source not in SOURCES:
        raise SdlcError("VALIDATION_FAILED", "Invalid status or source")
    tech = _clip(raw.get("technology"), 80)
    if not tech and status != "open":
        raise SdlcError("VALIDATION_FAILED", f"Say which technology the {layer_label(layer)} layer uses, or leave it open")
    extras = [_clip(x, 60) for x in (raw.get("extras") or []) if _clip(x, 60)][:8]
    component = _clip(raw.get("component"), 40)
    conf = str(raw.get("confidence") or "high").lower()
    return {
        "id": entry_id(layer, component), "layer": layer, "component": component, "technology": tech, "version": _clip(raw.get("version"), 30),
        "extras": extras, "notes": _clip(raw.get("notes"), 300), "status": status, "source": source, "sourceStage": stage,
        "rationale": _clip(raw.get("rationale"), 400), "evidence": _clip(raw.get("evidence"), 240),
        "confidence": conf if conf in CONFIDENCES else "medium", "updatedAt": now_iso(), "updatedBy": actor,
    }


def render_entry(e: dict[str, Any]) -> str:
    base = " ".join(x for x in (e.get("technology"), e.get("version")) if x)
    extras = ", ".join(e.get("extras") or [])
    return f"{base} + {extras}" if base and extras else (base or extras)


def summary_of(layers: list[dict[str, Any]]) -> str:
    """The one-line stack the rest of the platform reads: backend (or frontend) then the database, e.g. `Python 3.12 + FastAPI | PostgreSQL 16`."""
    def pick(layer: str) -> list[str]:
        return [render_entry(e) for e in layers if e["layer"] == layer and e["status"] != "open" and render_entry(e)]
    head = pick("backend") or pick("frontend")
    if not head:
        return ""
    db = pick("database")
    return "; ".join(head) + (f" | {'; '.join(db)}" if db else "")


def summary_source(layers: list[dict[str, Any]]) -> str:
    """'user' when a person pinned the backend, 'ta' when it was identified, '' when undecided (the legacy tech_stack_source)."""
    back = [e for e in layers if e["layer"] in ("backend", "frontend") and e["status"] != "open" and render_entry(e)]
    if not back:
        return ""
    return "user" if any(e["status"] == "pinned" for e in back) else "ta"


# ---------------------------------------------------------------- merging what the platform identified
def _rank(e: dict[str, Any]) -> int:
    return int(e.get("sourceStage") or 0)


def merge_identified(config: dict[str, Any], found: list[dict[str, Any]], *, stage: int, source: str, decider: bool = False) -> list[dict[str, Any]]:
    """Fold what the advisor found into the config (in place). Returns the changes made, for the audit log and the screen.

    A pinned value is never touched; a disagreement with it is recorded as a conflict. An open layer is filled. An identified value is
    replaced by the same stage or a later one (the Technical Architect, `decider`, always)."""
    stack = config["stack"]
    layers: list[dict[str, Any]] = stack["layers"]
    changes: list[dict[str, Any]] = []
    for f in found:
        f = {**f, "sourceStage": stage}
        cur = next((e for e in layers if e["id"] == f["id"]), None)
        if cur is None:
            # a pinned entry on the same layer with no component also covers a found entry with a component, and the reverse
            same_layer_pinned = next((e for e in layers if e["layer"] == f["layer"] and e["status"] == "pinned" and not e["component"] and not f["component"]), None)
            if same_layer_pinned:
                cur = same_layer_pinned
        if cur is None:
            if len(layers) >= MAX_ENTRIES:
                continue
            layers.append(f)
            changes.append({"layer": f["layer"], "from": "", "to": render_entry(f), "action": "added", "stage": stage})
            continue
        if cur["status"] == "pinned":
            if render_entry(cur).lower() != render_entry(f).lower() and cur["technology"].lower() != f["technology"].lower():
                conflict = {"layer": cur["layer"], "pinned": render_entry(cur), "found": render_entry(f), "stage": stage, "evidence": f.get("evidence", "")}
                if not any(c["layer"] == conflict["layer"] and c["found"] == conflict["found"] for c in stack["conflicts"]):
                    stack["conflicts"] = [*stack["conflicts"], conflict][-20:]
            continue
        if cur["status"] == "open" or decider or _rank(f) >= _rank(cur):
            if render_entry(cur) == render_entry(f) and cur["status"] != "open":
                cur.update(evidence=f.get("evidence") or cur.get("evidence"), rationale=f.get("rationale") or cur.get("rationale"), updatedAt=f["updatedAt"])
                continue
            changes.append({"layer": f["layer"], "from": render_entry(cur), "to": render_entry(f), "action": "filled" if cur["status"] == "open" else "revised", "stage": stage})
            layers[layers.index(cur)] = f
    return changes


# ---------------------------------------------------------------- what a stage is told
def relevant_layers(stage_template: int | None) -> tuple[str, ...]:
    return STAGE_LAYERS.get(int(stage_template or 0), tuple(layer_ids()))


_STATUS_NOTE = {"pinned": "pinned by the team", "identified": "identified in the project's documents", "open": "left open: decide it"}


def render_layers(layers: list[dict[str, Any]], stage_template: int | None) -> tuple[str, str]:
    """(decided layers, undecided layers) as prompt text for one stage. Both may be empty."""
    want = relevant_layers(stage_template)
    decided, open_layers = [], []
    for e in layers:
        if e["layer"] not in want:
            continue
        label = layer_label(e["layer"]) + (f" ({e['component']})" if e.get("component") else "")
        if e["status"] == "open" or not render_entry(e):
            open_layers.append(label)
            continue
        line = f"- {label}: {render_entry(e)} [{_STATUS_NOTE[e['status']]}]"
        if e.get("notes"):
            line += f". {e['notes']}"
        decided.append(line)
    have = {e["layer"] for e in layers if e["status"] != "open" and render_entry(e)}
    for lid in want:
        if lid not in have and layer_label(lid) not in open_layers:
            open_layers.append(layer_label(lid))
    return "\n".join(decided), ", ".join(open_layers)


def legacy_entries(tech_stack: str, source: str) -> list[dict[str, Any]]:
    """The old one-string stack as entries: `Python 3.12 + FastAPI | PostgreSQL 16` becomes a backend and a database entry."""
    text = (tech_stack or "").strip()
    if not text:
        return []
    status = "pinned" if source == "user" else "identified"
    head, _, store = text.partition("|")
    out: list[dict[str, Any]] = []
    parts = [p.strip() for p in re.split(r"\s*\+\s*", head) if p.strip()]
    if parts:
        m = re.match(r"^(?P<t>.*?)[ ]+(?P<v>v?\d[\w.\-]*(?:\s*\(LTS\))?)$", parts[0])
        tech, ver = (m.group("t"), m.group("v")) if m else (parts[0], "")
        out.append(normalise_entry({"layer": "backend", "technology": tech, "version": ver, "extras": parts[1:]}, status=status, source="legacy"))
    if store.strip():
        m = re.match(r"^(?P<t>.*?)[ ]+(?P<v>v?\d[\w.\-]*)$", store.strip())
        tech, ver = (m.group("t"), m.group("v")) if m else (store.strip(), "")
        out.append(normalise_entry({"layer": "database", "technology": tech, "version": ver}, status=status, source="legacy"))
    return out


# ---------------------------------------------------------------- the service
class ProjectConfigService:
    def __init__(self, db: Any, content: Any, authz: Any, audit: Any, canon: Any = None) -> None:
        self._db, self._content, self._authz, self._audit, self._canon = db, content, authz, audit, canon
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, project_id: str) -> asyncio.Lock:
        return self._locks.setdefault(project_id, asyncio.Lock())

    @staticmethod
    def file_key(project_id: str) -> str:
        return f"content-store/{project_id}/projectconfig.json"

    # ---- access
    async def can_edit(self, project_id: str, user: UserPublic) -> bool:
        if self._canon is None:
            return user.role == "SUPER_ADMIN"
        try:
            await self._canon.assert_can_author(project_id, user)
            return True
        except SdlcError:
            return False

    async def assert_can_edit(self, project_id: str, user: UserPublic) -> None:
        await self._authz.assert_project_access(project_id, user)
        if not await self.can_edit(project_id, user):
            raise SdlcError("FORBIDDEN", "Only the managing PM, a super-admin, or a solution or technical architect on the project can change the project config")

    # ---- read
    async def get(self, project_id: str) -> dict[str, Any]:
        """The project's config; created (empty, or from the project's legacy one-line stack) the first time it is read."""
        row = await self._db.get_project_config(project_id)
        if row is not None and row["config"].get("stack") is not None:
            cfg = row["config"]
            cfg["version"] = row["version"]
            return cfg
        # No lock here: callers that write hold it, and two first reads create the same empty document.
        cfg = empty_config()
        project = await self._db.get_project(project_id) or {}
        cfg["stack"]["layers"] = legacy_entries(str(project.get("tech_stack") or ""), str(project.get("tech_stack_source") or ""))
        return await self._write(project_id, cfg, actor="", sync_summary=False)

    async def layers(self, project_id: str) -> list[dict[str, Any]]:
        try:
            return list((await self.get(project_id))["stack"]["layers"])
        except Exception:  # noqa: BLE001 - prompts fall back to the one-line stack
            log.warning("could not read the project config", exc_info=True)
            return []

    async def ensure(self, project_id: str) -> None:
        """Create the empty config (and its file) for a new project."""
        await self.get(project_id)

    # ---- write
    async def _write(self, project_id: str, cfg: dict[str, Any], *, actor: str, sync_summary: bool = True) -> dict[str, Any]:
        stack = cfg["stack"]
        stack["summary"] = summary_of(stack["layers"])
        cfg["updatedAt"] = now_iso()
        prior = await self._db.get_project_config(project_id)
        cfg["version"] = (int(prior["version"]) + 1) if prior else 1
        await self._db.save_project_config(project_id, cfg, cfg["version"], actor or None)
        try:
            await self._content.put(self.file_key(project_id), json.dumps(cfg, indent=2, ensure_ascii=False))
        except Exception:  # noqa: BLE001 - the table is the source of truth; the file is a mirror
            log.warning("could not write projectconfig.json", exc_info=True)
        if sync_summary:
            await self._db.set_project_stack(project_id, stack["summary"], summary_source(stack["layers"]))
        return cfg

    async def update(self, project_id: str, user: UserPublic, *, upsert: list[dict[str, Any]], remove: list[str]) -> dict[str, Any]:
        """A person's edit: every upserted entry becomes pinned (or open when `status` is 'open'); removed ids are dropped."""
        await self.assert_can_edit(project_id, user)
        async with self._lock(project_id):
            cfg = await self.get(project_id)
            layers = cfg["stack"]["layers"]
            changes: list[dict[str, Any]] = []
            for rid in remove:
                gone = next((e for e in layers if e["id"] == rid), None)
                if gone:
                    layers.remove(gone)
                    changes.append({"layer": gone["layer"], "from": render_entry(gone), "to": "", "action": "removed"})
            for raw in upsert:
                status = "open" if str(raw.get("status") or "") == "open" else "pinned"
                e = normalise_entry(raw, status=status, source="user", actor=user.email)
                old_id = str(raw.get("id") or "")
                cur = next((x for x in layers if x["id"] in (e["id"], old_id)), None)
                if cur is None and len(layers) >= MAX_ENTRIES:
                    raise SdlcError("VALIDATION_FAILED", f"At most {MAX_ENTRIES} stack entries")
                if cur is not None:
                    layers[layers.index(cur)] = e
                else:
                    layers.append(e)
                changes.append({"layer": e["layer"], "from": render_entry(cur) if cur else "", "to": render_entry(e), "action": "set" if status == "pinned" else "left open"})
            cfg["stack"]["conflicts"] = [c for c in cfg["stack"]["conflicts"]
                                         if any(e["layer"] == c["layer"] and e["status"] == "pinned" and render_entry(e) == c["pinned"] for e in layers)]
            cfg = await self._write(project_id, cfg, actor=user.email)
        self._audit_change(project_id, user.email, changes, via="screen")
        return cfg

    async def apply_found(self, project_id: str, found: list[dict[str, Any]], *, stage: int, source: str, decider: bool = False, actor: str = "platform",
                          run: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """What the advisor (or the codebase detector) found. Never raises; returns the changes made."""
        try:
            async with self._lock(project_id):
                cfg = await self.get(project_id)
                entries = []
                for f in found:
                    try:
                        entries.append(normalise_entry(f, status="identified", source=source, actor=actor, stage=stage))
                    except SdlcError:
                        continue
                changes = merge_identified(cfg, entries, stage=stage, source=source, decider=decider)
                if run is not None:
                    cfg["stack"]["advisor"][str(stage)] = {**run, "at": now_iso(), "found": len(entries), "changed": len(changes)}
                if changes or run is not None:
                    await self._write(project_id, cfg, actor=actor)
            if changes:
                self._audit_change(project_id, actor, changes, via=f"stage {stage}", phase=stage)
            return changes
        except Exception:  # noqa: BLE001 - identifying the stack must never fail a stage
            log.warning("could not apply the identified stack", exc_info=True)
            return []

    async def confirm(self, project_id: str, user: UserPublic, ids: list[str]) -> dict[str, Any]:
        """Pin identified entries as they are ("Confirm")."""
        await self.assert_can_edit(project_id, user)
        async with self._lock(project_id):
            cfg = await self.get(project_id)
            changes = []
            for e in cfg["stack"]["layers"]:
                if e["id"] in ids and e["status"] == "identified":
                    e.update(status="pinned", source="user", updatedBy=user.email, updatedAt=now_iso())
                    changes.append({"layer": e["layer"], "from": render_entry(e), "to": render_entry(e), "action": "confirmed"})
            cfg = await self._write(project_id, cfg, actor=user.email)
        self._audit_change(project_id, user.email, changes, via="screen")
        return cfg

    async def set_backend_text(self, project_id: str, text: str, *, source: str, actor: str) -> None:
        """The legacy one-string setters (clarification answer, "Set manually"): the text becomes the backend (and database) entries."""
        entries = legacy_entries(text, source)
        async with self._lock(project_id):
            cfg = await self.get(project_id)
            keep = [e for e in cfg["stack"]["layers"] if e["layer"] not in ("backend", "database") or e.get("component")]
            for e in entries:
                e["source"] = "user" if source == "user" else "ta"
                e["updatedBy"] = actor
            cfg["stack"]["layers"] = [*keep, *entries]
            await self._write(project_id, cfg, actor=actor)

    def _audit_change(self, project_id: str, actor: str, changes: list[dict[str, Any]], *, via: str, phase: int | None = None) -> None:
        if not changes:
            return
        self._audit.record(project_id=project_id, phase=phase, agent_role="Orchestrator", event="project.config_updated",
                           human_reviewer=actor if "@" in actor else None, detail={"via": via, "changes": changes[:30]})
