"""The start-release questionnaire.

Starting a release asks a FIXED, versioned set of questions with enumerated answers. Every answer maps to a defined
action in code: there is no model deciding what happens. (A model only RANKS carry-set suggestions, and a person
confirms the final list.) The flow is: `questions` → `preview` (validates, reports exactly what will happen, changes
nothing) → `start` (applies it, step by step, each step idempotent, progress recorded on the release so an
interrupted start can be resumed)."""

from __future__ import annotations

import re
from typing import Any

from ..domain.errors import SdlcError
from ..domain.models import UserPublic
from .carry import MAX_BYTES, MAX_ITEMS
from .templates import PRESETS

QUESTIONNAIRE_VERSION = 1
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")


def question_list() -> list[dict[str, Any]]:
    """The questionnaire, as data (the UI renders it; `when` says when a question applies)."""
    return [
        {"id": "name", "type": "text", "required": True, "max": 120, "title": "Release name"},
        {"id": "goal", "type": "text", "max": 400, "title": "What is this release for?",
         "help": "New requirements and scope. It is also what DevMind uses to suggest context to carry over."},
        {"id": "startFrom", "type": "choice", "default": "blank", "title": "Start from",
         "options": [{"id": "blank", "label": "A blank release"},
                     {"id": "fork", "label": "A fork of an earlier release (at its last closed sprint)"}]},
        {"id": "sourceRelease", "type": "release", "when": {"startFrom": "fork"}, "title": "Fork from release"},
        {"id": "carry", "type": "carry", "when": {"startFrom": "fork"}, "title": "Context to carry over",
         "help": f"Only what you choose is copied in (up to {MAX_ITEMS} entries / {MAX_BYTES // 1000} KB). Nothing is merged back."},
        {"id": "unfinishedItems", "type": "choice", "default": "none", "when": {"startFrom": "fork"},
         "title": "Unfinished backlog items of the source release",
         "options": [{"id": "none", "label": "Leave them in the source release"},
                     {"id": "all", "label": "Move all of them (Jira keys are kept)"},
                     {"id": "selected", "label": "Move the ones I pick"}]},
        {"id": "stagePreset", "type": "choice", "default": "inherit", "title": "Stages this release runs",
         "options": [{"id": k, "label": v["label"]} for k, v in PRESETS.items()]
         + [{"id": "custom", "label": "Choose the stages myself"}]},
        {"id": "intakeRule", "type": "choice", "default": "pool", "title": "Where do new Jira issues go?",
         "options": [{"id": "pool", "label": "To the shared unassigned pool"},
                     {"id": "epic", "label": "To this release, when their epic is mapped to it"}]},
        {"id": "epics", "type": "epics", "when": {"intakeRule": "epic"}, "title": "Epics that belong to this release"},
        {"id": "usePool", "type": "bool", "default": True, "title": "May sprint planning draw on the shared pool?"},
        {"id": "jiraLabel", "type": "text", "max": 40, "optional": True, "title": "Label for moved and claimed items",
         "help": "Added to the items' labels (and written through to Jira)."},
        {"id": "firstSprint", "type": "choice", "default": "later", "title": "First sprint",
         "options": [{"id": "later", "label": "Start it later"}, {"id": "now", "label": "Start it now"}]},
    ]


def normalise(answers: dict[str, Any], defaults: dict[str, Any], locks: list[str]) -> dict[str, Any]:
    """Defaults first, then the person's answers, then locked questions forced back to their default."""
    out: dict[str, Any] = {
        "startFrom": "blank", "unfinishedItems": "none", "stagePreset": "inherit", "intakeRule": "pool",
        "usePool": True, "firstSprint": "later", "carry": [], "items": [], "epics": [], "adoptExistingEpicItems": False,
        "doneItems": "context"}
    out.update({k: v for k, v in defaults.items() if k in out or k in ("name", "goal", "jiraLabel")})
    out.update({k: v for k, v in answers.items() if v is not None})
    for q in locks:
        if q in defaults:
            out[q] = defaults[q]
    return out


def validate(a: dict[str, Any]) -> list[str]:
    """Pure checks of the shape of the answers (database checks happen in the service)."""
    errors: list[str] = []
    name = str(a.get("name", "")).strip()
    if not 1 <= len(name) <= 120:
        errors.append("name: 1-120 characters")
    if len(str(a.get("goal", ""))) > 400:
        errors.append("goal: at most 400 characters")
    if a["startFrom"] not in ("blank", "fork"):
        errors.append("startFrom: blank or fork")
    if a["startFrom"] == "fork" and not a.get("sourceRelease"):
        errors.append("sourceRelease: choose the release to fork")
    if a["unfinishedItems"] not in ("none", "all", "selected"):
        errors.append("unfinishedItems: none, all or selected")
    if a["unfinishedItems"] != "none" and a["startFrom"] != "fork":
        errors.append("unfinishedItems: only a fork has a source release to move items from")
    if a["unfinishedItems"] == "selected" and not a.get("items"):
        errors.append("items: pick at least one item to move")
    if a["stagePreset"] not in (*PRESETS, "custom"):
        errors.append(f"stagePreset: unknown '{a['stagePreset']}'")
    if a["stagePreset"] == "custom" and not a.get("stages"):
        errors.append("stages: a custom stage set needs stages")
    if a["intakeRule"] not in ("pool", "epic"):
        errors.append("intakeRule: pool or epic")
    if a["intakeRule"] == "pool" and a.get("epics"):
        errors.append("epics: only used when new issues are routed by epic")
    if a["firstSprint"] not in ("later", "now"):
        errors.append("firstSprint: later or now")
    if a.get("carry") and a["startFrom"] != "fork":
        errors.append("carry: only a fork can carry context over")
    if len(a.get("carry") or []) > MAX_ITEMS:
        errors.append(f"carry: at most {MAX_ITEMS} entries")
    label = a.get("jiraLabel")
    if label and not _LABEL.match(str(label)):
        errors.append("jiraLabel: letters, digits, '_', '.', '-' (max 40)")
    return errors


class ReleaseSetupService:
    def __init__(self, db: Any, audit: Any, agile: Any, backlog: Any, carry: Any, index: Any) -> None:
        self._db, self._audit, self._agile, self._backlog, self._carry, self._index = db, audit, agile, backlog, carry, index

    # ------------------------------------------------------------------ questions
    async def questions(self, project_id: str, user: UserPublic) -> dict[str, Any]:
        await self._agile.assert_can_run(project_id, user)
        cfg = await self._agile._settings(project_id)
        releases = [{"id": r["id"], "code": r["code"], "name": r["name"], "status": r["status"]}
                    for r in await self._db.list_releases(project_id)]
        closed = {i["release_id"] for i in await self._db.list_iterations(project_id) if i["status"] == "closed"}
        for r in releases:
            r["canFork"] = r["id"] in closed
        return {"version": QUESTIONNAIRE_VERSION, "questions": question_list(), "releases": releases,
                "defaults": cfg["release_defaults"] or {}, "locked": list(cfg["release_locks"] or [])}

    async def _answers(self, project_id: str, raw: dict[str, Any]) -> dict[str, Any]:
        cfg = await self._agile._settings(project_id)
        return normalise(raw, cfg["release_defaults"] or {}, list(cfg["release_locks"] or []))

    # ------------------------------------------------------------------ preview (changes nothing)
    async def preview(self, project_id: str, user: UserPublic, raw: dict[str, Any]) -> dict[str, Any]:
        await self._agile.assert_can_run(project_id, user)
        a = await self._answers(project_id, raw)
        errors = validate(a)
        warnings: list[str] = []
        steps: list[str] = []
        summary: dict[str, Any] = {}
        source = None
        if not errors:
            source, errs = await self._source(project_id, a)
            errors += errs
        if not errors:
            steps.append(f"Create release “{a['name'].strip()}”" + (f" as a fork of {source['code']}" if source else ""))
            if source:
                last = max((i for i in await self._db.list_iterations(project_id)
                            if i["release_id"] == source["id"] and i["status"] == "closed"), key=lambda i: i["number"])
                steps.append(f"Baseline: {source['code']} at {last['label']} — nothing is merged back")
                chosen, rejected = await self._check_carry(project_id, source, a)
                summary["carry"] = {"entries": len(chosen), "bytes": sum(len(c.text.encode()) for c in chosen),
                                    "rejected": rejected}
                warnings += [f"{r['id']}: {r['reason']}" for r in rejected]
                steps.append(f"Carry {len(chosen)} context entr{'y' if len(chosen) == 1 else 'ies'} into the new release"
                             if chosen else "Carry no context (a blank plate)")
                moved = await self._movable(project_id, source, a)
                summary["move"] = {"items": [r["item_key"] for r in moved["rows"]], "skipped": moved["skipped"]}
                warnings += [f"{s['id']}: {s['reason']}" for s in moved["skipped"]]
                if a["unfinishedItems"] != "none":
                    steps.append(f"Move {len(moved['rows'])} unfinished item(s) from {source['code']} (Jira keys kept)")
            steps.append("Stage set: " + (PRESETS[a["stagePreset"]]["label"] if a["stagePreset"] in PRESETS else "custom"))
            steps.append("New Jira issues: " + ("routed to this release by epic, otherwise to the shared pool"
                                                if a["intakeRule"] == "epic" else "go to the shared unassigned pool"))
            if a["intakeRule"] == "epic":
                errors += await self._db_errors(project_id, a, source)
                ep = []
                for ref in a["epics"]:
                    row = await self._db.get_backlog_item(project_id, ref)
                    if row is None or row["type"] != "epic":
                        continue
                    kids = [r for r in await self._db.list_backlog(project_id, scope="pool") if r["epic_id"] == row["id"]]
                    ep.append({"epic": row["item_key"], "poolItems": len(kids)})
                summary["epics"] = ep
                if ep:
                    steps.append("Map epics: " + ", ".join(f"{e['epic']} ({e['poolItems']} pool item(s))" for e in ep))
            elif source is not None and a["unfinishedItems"] == "selected":
                errors += await self._db_errors(project_id, a, source)
            if a.get("jiraLabel"):
                steps.append(f"Label moved items “{a['jiraLabel']}”")
            if a["firstSprint"] == "now":
                steps.append("Start the first sprint now")
        return {"valid": not errors, "errors": errors, "warnings": warnings, "steps": steps, "summary": summary,
                "answers": {k: v for k, v in a.items() if k != "stages"}}

    # ------------------------------------------------------------------ helpers
    async def _source(self, project_id: str, a: dict[str, Any]) -> tuple[Any | None, list[str]]:
        if a["startFrom"] != "fork":
            return None, []
        src = await self._db.get_release(a["sourceRelease"])
        if not src or src["project_id"] != project_id:
            return None, ["sourceRelease: release not found"]
        if not any(i["release_id"] == src["id"] and i["status"] == "closed" for i in await self._db.list_iterations(project_id)):
            return None, [f"sourceRelease: {src['code']} has no closed sprint yet, so there is nothing stable to fork from"]
        return src, []

    async def _db_errors(self, project_id: str, a: dict[str, Any], source: Any | None, release: Any | None = None) -> list[str]:
        """Checks that need the database (so `start` refuses up front what `preview` would have reported):
        the epics exist, are epics and are free to map; the items to move are really unfinished items of the source."""
        errors: list[str] = []
        if a["intakeRule"] == "epic":
            for ref in a.get("epics") or []:
                row = await self._db.get_backlog_item(project_id, ref)
                if row is None or row["type"] != "epic":
                    errors.append(f"epics: '{ref}' is not an epic of this project")
                    continue
                cur = await self._db.epic_mapping(row["id"])
                if cur is not None and cur["status"] != "closed" and (release is None or cur["release_id"] != release["id"]):
                    errors.append(f"epics: {row['item_key']} already belongs to release {cur['code']}; unmap it there first")
        if source is not None and a["unfinishedItems"] == "selected":
            mv = await self._movable(project_id, source, a)
            errors += [f"items: {sk['id']} — {sk['reason']}" for sk in mv["skipped"]]
        return errors

    async def _check_carry(self, project_id: str, source: Any, a: dict[str, Any]) -> tuple[list[Any], list[dict[str, str]]]:
        from .carry import select
        cands = {c.id: c for c in await self._carry.candidates(project_id, source)}
        if a.get("doneItems") == "none":          # keep the epics, leave out the delivered stories
            cands = {k: v for k, v in cands.items() if not (v.carriedFrom and v.carriedFrom.path == "backlog/story")}
        return select(cands, list(a.get("carry") or []), set())

    async def _movable(self, project_id: str, source: Any, a: dict[str, Any]) -> dict[str, Any]:
        if a["unfinishedItems"] == "none":
            return {"rows": [], "skipped": []}
        rows = [r for r in await self._db.list_backlog(project_id, release_id=source["id"], scope="release")
                if r["type"] != "epic" and r["status"] in ("new", "refined", "ready") and r["iteration_id"] is None]
        skipped: list[dict[str, str]] = []
        if a["unfinishedItems"] == "selected":
            want = set(a.get("items") or [])
            have = {r["item_key"] for r in rows}
            skipped = [{"id": k, "reason": "not an unfinished item of the source release"} for k in sorted(want - have)]
            rows = [r for r in rows if r["item_key"] in want]
        return {"rows": rows, "skipped": skipped}

    # ------------------------------------------------------------------ start
    async def start(self, project_id: str, user: UserPublic, raw: dict[str, Any], *,
                    resume_release_id: str | None = None) -> dict[str, Any]:
        """Apply the answers. Validation first (nothing changes when the preview is not valid); then the steps in order,
        each idempotent, with progress kept on the release: an interrupted start is resumed by calling again with
        `resume_release_id`."""
        await self._agile.assert_can_run(project_id, user)
        rel = None
        if resume_release_id:
            # Resume repeats the STORED setup: the answers it was started with, only for a release whose setup is part-way.
            rel = await self._db.get_release(resume_release_id)
            if not rel or rel["project_id"] != project_id:
                raise SdlcError("NOT_FOUND", "Release not found")
            stored = rel["setup"] or {}
            if stored.get("status") == "complete" or not stored.get("answers"):
                raise SdlcError("GATE_CONFLICT", f"Release {rel['code']} was not left part-way by the questionnaire; there is nothing to resume")
            a = dict(stored["answers"])
        else:
            a = await self._answers(project_id, raw)
        done_steps = dict((rel["setup"] or {}).get("progress") or {}) if rel else {}
        errors = validate(a)
        source, errs = await self._source(project_id, a)
        errors += errs
        if not errors:
            errors += await self._db_errors(project_id, a, source, rel)
        if errors:
            raise SdlcError("VALIDATION_FAILED", " | ".join(errors[:6]), {"errors": errors})
        if source and not done_steps.get("carry"):
            chosen, rejected = await self._check_carry(project_id, source, a)
            if rejected:
                raise SdlcError("VALIDATION_FAILED", "The carry set is not valid: " + "; ".join(
                    f"{r['id']} ({r['reason']})" for r in rejected[:5]), {"rejected": rejected})
        # 1. the release itself
        if rel is None:
            made = await self._agile.create_release(
                project_id, user, name=a["name"], goal=str(a.get("goal", "")), forked_from=source["id"] if source else None,
                stage_preset=a["stagePreset"], stages=a.get("stages") if a["stagePreset"] == "custom" else None,
                intake_rule=a["intakeRule"], use_pool=bool(a["usePool"]),
                setup={"version": QUESTIONNAIRE_VERSION, "answers": a, "progress": {"created": True}, "by": user.email})
            rel = await self._db.get_release(made["id"])
        progress: dict[str, Any] = dict((rel["setup"] or {}).get("progress") or {"created": True})
        setup = dict(rel["setup"] or {})

        async def done(step: str, value: Any = True) -> None:
            progress[step] = value
            setup["progress"] = progress
            await self._db.update_release(rel["id"], setup=setup)

        try:
            # 2. the carried context (files)
            if source and not progress.get("carry"):
                res = await self._carry.apply(project_id, rel, source, list(a.get("carry") or []), actor=user.email)
                await done("carry", {"carried": len(res["carried"])})
            elif not progress.get("index"):
                await self._index.stage_release(project_id, rel["id"], closed=False)
            await done("index")
            # 3. unfinished items move (Jira keys kept), optionally labelled
            if source and a["unfinishedItems"] != "none" and not progress.get("moved"):
                mv = await self._movable(project_id, source, a)
                res = await self._backlog.move_unfinished(project_id, user, source["id"], rel["id"],
                                                          [r["item_key"] for r in mv["rows"]] if mv["rows"] else [])
                if a.get("jiraLabel"):
                    await self._label(project_id, res["moved"], a["jiraLabel"])
                await done("moved", {"moved": len(res["moved"])})
            # 4. epics → this release
            if a["intakeRule"] == "epic" and a["epics"] and not progress.get("epics"):
                for ref in a["epics"]:
                    await self._backlog.map_epic(project_id, user, rel["id"], ref,
                                                 adopt_existing=bool(a.get("adoptExistingEpicItems")))
                await done("epics", len(a["epics"]))
            # 5. the first sprint
            if a["firstSprint"] == "now" and not progress.get("sprint"):
                await self._agile.start_sprint(project_id, user, release_id=rel["id"])
                await done("sprint")
        except Exception as err:
            self._audit.record(project_id=project_id, agent_role="Agile", event="release.setup_incomplete",
                               human_reviewer=user.email, detail={"release": rel["code"], "progress": progress})
            # The release exists and is part-way set up: tell the caller how to resume instead of leaving it guessing.
            code = err.code if isinstance(err, SdlcError) else "INTERNAL"
            msg = err.message if isinstance(err, SdlcError) else f"Starting the release stopped part-way: {str(err)[:200]}"
            raise SdlcError(code, msg, {**(err.details if isinstance(err, SdlcError) else {}),
                                        "releaseId": rel["id"], "progress": progress, "resumable": True}) from err
        setup["status"] = "complete"
        await self._db.update_release(rel["id"], setup=setup)
        self._audit.record(project_id=project_id, agent_role="Agile", event="release.setup_complete",
                           human_reviewer=user.email, detail={"release": rel["code"], "progress": progress})
        by_id = {r["id"]: r for r in await self._db.list_releases(project_id)}
        open_it = await self._db.get_open_iteration(project_id, rel["id"])
        return {"release": self._agile._release_view(await self._db.get_release(rel["id"]), by_id, open_it),
                "progress": progress}

    async def _label(self, project_id: str, keys: list[str], label: str) -> None:
        for key in keys:
            row = await self._db.get_backlog_item(project_id, key)
            if row is None or label in (row["labels"] or []):
                continue
            new = await self._db.update_backlog_item(project_id, row["id"], expected_version=None,
                                                     labels=[*(row["labels"] or []), label][:20])
            if new is not None and new["jira_key"]:
                await self._backlog._after_write(project_id, new, "update")
