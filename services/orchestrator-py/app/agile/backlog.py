"""BacklogService — the product backlog with enforced rules, authorisation and audit."""

from __future__ import annotations

from typing import Any

from ..domain.errors import SdlcError
from ..domain.models import UserPublic
from .rules import (
    COMMITTED, ITEM_TYPES, STATUSES, check_transition, clean_components, clean_criteria, points, ready_problems,
    validate_estimate,
)

CONTENT_FIELDS = {"title", "description", "acceptanceCriteria", "estimate", "components", "labels", "type", "epicKey"}
MAX_TITLE, MAX_DESC = 200, 8000


def item_view(row: Any, epic_key: str | None = None) -> dict[str, Any]:
    return {
        "id": row["id"], "key": row["item_key"], "type": row["type"], "title": row["title"],
        "description": row["description"], "acceptanceCriteria": list(row["acceptance_criteria"] or []),
        "estimate": float(row["estimate"]) if row["estimate"] is not None else None,
        "rank": float(row["rank"]), "status": row["status"], "epicKey": epic_key,
        "components": list(row["components"] or []), "labels": list(row["labels"] or []),
        "iterationId": row["iteration_id"], "jiraKey": row["jira_key"], "version": row["version"],
        "problems": ready_problems(row) if row["status"] in ("new", "refined", "ready") else [],
        "createdAt": row["created_at"].isoformat(), "updatedAt": row["updated_at"].isoformat(),
    }


class BacklogService:
    def __init__(self, db: Any, audit: Any, authz: Any, agile: Any) -> None:
        self._db, self._audit, self._authz, self._agile = db, audit, authz, agile
        self.sync_hook: Any = None   # JiraSync write-through; set by the composition root (None = off)

    # ------------------------------------------------------------------ auth
    async def _can_edit(self, project_id: str, user: UserPublic) -> bool:
        try:
            await self._agile.assert_can_run(project_id, user)
            return True
        except SdlcError:
            return False

    async def _assert_edit(self, project_id: str, user: UserPublic) -> None:
        if not await self._can_edit(project_id, user):
            raise SdlcError("FORBIDDEN", "Only the Product Owner or the managing Project Manager can change the backlog")

    async def _assert_member(self, project_id: str, user: UserPublic) -> None:
        if await self._can_edit(project_id, user):
            return
        if await self._authz.get_membership_role(project_id, user.id) is None:
            raise SdlcError("FORBIDDEN", "You are not a member of this project")

    # ------------------------------------------------------------------ reads
    async def list(self, project_id: str, user: UserPublic, *, status: list[str] | None = None,
                   iteration_id: str | None = None, q: str | None = None) -> dict[str, Any]:
        await self._authz.assert_project_access(project_id, user)
        for s in status or []:
            if s not in STATUSES:
                raise SdlcError("VALIDATION_FAILED", f"unknown status '{s}'")
        rows = await self._db.list_backlog(project_id, statuses=status, iteration_id=iteration_id)
        everything = rows if not (status or iteration_id) else await self._db.list_backlog(project_id)
        keys = {r["id"]: r["item_key"] for r in everything}
        if q:
            ql = q.lower().strip()
            rows = [r for r in rows if ql in r["title"].lower() or ql in r["item_key"].lower()
                    or ql in (r["description"] or "").lower()]
        return {
            "items": [item_view(r, keys.get(r["epic_id"])) for r in rows],
            "summary": {"count": len(rows), "points": points([r for r in rows if r["type"] != "epic"])},
        }

    async def get(self, project_id: str, user: UserPublic, ref: str) -> dict[str, Any]:
        await self._authz.assert_project_access(project_id, user)
        row = await self._row(project_id, ref)
        return item_view(row, await self._epic_key(project_id, row))

    async def _row(self, project_id: str, ref: str) -> Any:
        row = await self._db.get_backlog_item(project_id, ref)
        if row is None:
            raise SdlcError("NOT_FOUND", f"Backlog item '{ref}' not found")
        return row

    async def _epic_key(self, project_id: str, row: Any) -> str | None:
        if not row["epic_id"]:
            return None
        epic = await self._db.get_backlog_item(project_id, row["epic_id"])
        return epic["item_key"] if epic else None

    # ------------------------------------------------------------------ validation of input
    async def _clean(self, project_id: str, data: dict[str, Any], *, partial: bool) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if "title" in data or not partial:
            t = " ".join(str(data.get("title", "")).split())
            if not t or len(t) > MAX_TITLE:
                raise SdlcError("VALIDATION_FAILED", f"title is required (max {MAX_TITLE} characters)")
            out["title"] = t
        if "description" in data:
            d = str(data["description"] or "")
            if len(d) > MAX_DESC:
                raise SdlcError("VALIDATION_FAILED", f"description is too long (max {MAX_DESC})")
            out["description"] = d
        if "type" in data:
            if data["type"] not in ITEM_TYPES:
                raise SdlcError("VALIDATION_FAILED", f"type must be one of {', '.join(ITEM_TYPES)}")
            out["type"] = data["type"]
        if "acceptanceCriteria" in data:
            out["acceptance_criteria"] = clean_criteria(data["acceptanceCriteria"])
        if "estimate" in data:
            try:
                out["estimate"] = validate_estimate(data["estimate"])
            except ValueError as err:
                raise SdlcError("VALIDATION_FAILED", str(err)) from err
        if "components" in data:
            out["components"] = clean_components(data["components"])
        if "labels" in data:
            out["labels"] = [str(x).strip()[:40] for x in (data["labels"] or []) if str(x).strip()][:20]
        if "epicKey" in data:
            if data["epicKey"]:
                epic = await self._db.get_backlog_item(project_id, data["epicKey"])
                if epic is None or epic["type"] != "epic":
                    raise SdlcError("VALIDATION_FAILED", f"'{data['epicKey']}' is not an epic in this project")
                out["epic_id"] = epic["id"]
            else:
                out["epic_id"] = None
        return out

    # ------------------------------------------------------------------ writes
    async def create(self, project_id: str, user: UserPublic, data: dict[str, Any]) -> dict[str, Any]:
        await self._assert_edit(project_id, user)
        f = await self._clean(project_id, {"type": "story", **data}, partial=False)
        if f.get("type") == "epic":
            f.pop("estimate", None)
        f["status"] = "refined" if (f.get("type") == "epic" or f.get("acceptance_criteria")) else "new"
        row = await self._db.insert_backlog_item(project_id=project_id, created_by=user.id, **f)
        self._audit.record(project_id=project_id, agent_role="Backlog", event="backlog.created",
                           human_reviewer=user.email, detail={"key": row["item_key"], "type": row["type"]})
        await self._after_write(project_id, row, "create")
        return item_view(row, await self._epic_key(project_id, row))

    async def update(self, project_id: str, user: UserPublic, ref: str, changes: dict[str, Any], *,
                     expected_version: int | None) -> dict[str, Any]:
        await self._assert_edit(project_id, user)
        bad = set(changes) - CONTENT_FIELDS
        if bad:
            raise SdlcError("VALIDATION_FAILED", f"cannot change: {', '.join(sorted(bad))}")
        row = await self._row(project_id, ref)
        if row["status"] == "dropped":
            raise SdlcError("GATE_CONFLICT", f"{row['item_key']} is dropped; restore it first")
        f = await self._clean(project_id, changes, partial=True)
        if (f.get("type") or row["type"]) == "epic" and row["iteration_id"]:
            raise SdlcError("VALIDATION_FAILED", "an item in a sprint cannot become an epic")
        new = await self._db.update_backlog_item(project_id, row["id"], expected_version=expected_version, **f)
        if new is None:
            raise SdlcError("GATE_CONFLICT", f"{row['item_key']} was changed by someone else — reload and try again")
        self._audit.record(project_id=project_id, agent_role="Backlog", event="backlog.updated",
                           human_reviewer=user.email, detail={
                               "key": row["item_key"], "fields": sorted(changes),
                               "afterCommitment": row["status"] in COMMITTED})
        await self._after_write(project_id, new, "update")
        return item_view(new, await self._epic_key(project_id, new))

    async def set_status(self, project_id: str, user: UserPublic, ref: str, target: str, *,
                         expected_version: int | None) -> dict[str, Any]:
        row = await self._row(project_id, ref)
        why = check_transition(row["status"], target)
        if why:
            raise SdlcError("GATE_CONFLICT", why)
        if target == row["status"]:
            return item_view(row, await self._epic_key(project_id, row))
        # who may do what
        if target == "in_progress":
            await self._assert_member(project_id, user)
        else:
            await self._assert_edit(project_id, user)
        if target in ("ready", "in_sprint"):
            problems = ready_problems(row)
            if problems:
                raise SdlcError("VALIDATION_FAILED", f"{row['item_key']} is not ready: {'; '.join(problems)}")
        if target == "in_sprint":
            raise SdlcError("GATE_CONFLICT", "Use 'add to sprint' to commit an item (it checks capacity)")
        if target == "in_progress":
            cfg = await self._db.get_project_agile(project_id)
            if cfg and cfg["wip_limit"]:
                wip = [r for r in await self._db.list_backlog(project_id, statuses=["in_progress"])]
                if len(wip) >= cfg["wip_limit"] and row["status"] != "in_progress":
                    raise SdlcError("GATE_CONFLICT",
                                    f"WIP limit reached ({cfg['wip_limit']}): finish something before starting {row['item_key']}")
        fields: dict[str, Any] = {"status": target}
        if row["status"] in ("in_sprint", "in_progress") and target in ("ready", "dropped"):
            fields["iteration_id"] = None                      # leaving the sprint
        if target == "dropped" and row["status"] in COMMITTED:
            raise SdlcError("GATE_CONFLICT", "Remove the item from the sprint before dropping it")
        new = await self._db.update_backlog_item(project_id, row["id"], expected_version=expected_version, **fields)
        if new is None:
            raise SdlcError("GATE_CONFLICT", f"{row['item_key']} was changed by someone else — reload and try again")
        self._audit.record(project_id=project_id, agent_role="Backlog", event="backlog.status",
                           human_reviewer=user.email, detail={"key": row["item_key"], "from": row["status"], "to": target})
        await self._after_write(project_id, new, "status")
        return item_view(new, await self._epic_key(project_id, new))

    async def move(self, project_id: str, user: UserPublic, ref: str, *, before: str | None = None,
                   after: str | None = None) -> dict[str, Any]:
        await self._assert_edit(project_id, user)
        if before and after:
            raise SdlcError("VALIDATION_FAILED", "give either before or after, not both")
        row = await self._row(project_id, ref)
        other = await self._row(project_id, before or after) if (before or after) else None
        try:
            new = await self._db.move_backlog_item(
                project_id, row["id"], before_id=other["id"] if before else None,
                after_id=other["id"] if after else None)
        except LookupError as err:
            raise SdlcError("NOT_FOUND", str(err)) from err
        self._audit.record(project_id=project_id, agent_role="Backlog", event="backlog.ranked",
                           human_reviewer=user.email, detail={"key": row["item_key"], "before": before, "after": after})
        return item_view(new, await self._epic_key(project_id, new))

    # ------------------------------------------------------------------ sprint commitment
    async def add_to_sprint(self, project_id: str, user: UserPublic, ref: str, *, force: bool = False) -> dict[str, Any]:
        """Commit a ready item to the open sprint. Capacity is enforced; going over needs `force` and is audited."""
        await self._assert_edit(project_id, user)
        it = await self._db.get_open_iteration(project_id)
        if it is None:
            raise SdlcError("GATE_CONFLICT", "There is no open sprint to add to")
        row = await self._row(project_id, ref)
        if row["status"] != "ready":
            raise SdlcError("GATE_CONFLICT", f"{row['item_key']} is '{row['status']}'; only ready items can be added")
        problems = ready_problems(row)
        if problems:
            raise SdlcError("VALIDATION_FAILED", f"{row['item_key']} is not ready: {'; '.join(problems)}")
        current = points(await self._db.list_backlog(project_id, iteration_id=it["id"]))
        total = current + float(row["estimate"])
        over = total > float(it["capacity"])
        if over and not force:
            raise SdlcError("GATE_CONFLICT",
                            f"Adding {row['item_key']} ({float(row['estimate']):g} pts) would put the sprint at {total:g} of "
                            f"{float(it['capacity']):g} points. Confirm to overcommit.")
        n = await self._db.assign_items_to_iteration(project_id, [row["id"]], it["id"], "in_sprint")
        if not n:
            raise SdlcError("GATE_CONFLICT", "The item could not be added")
        self._audit.record(project_id=project_id, agent_role="Backlog", event="sprint.item_added",
                           human_reviewer=user.email, detail={"key": row["item_key"], "sprint": it["label"],
                                                              "overcommit": over, "points": total})
        return item_view(await self._row(project_id, ref))

    async def remove_from_sprint(self, project_id: str, user: UserPublic, ref: str) -> dict[str, Any]:
        await self._assert_edit(project_id, user)
        row = await self._row(project_id, ref)
        if row["status"] != "in_sprint":
            raise SdlcError("GATE_CONFLICT", f"{row['item_key']} is '{row['status']}'; only items not yet started can leave the sprint")
        new = await self._db.update_backlog_item(project_id, row["id"], expected_version=None,
                                                 status="ready", iteration_id=None)
        self._audit.record(project_id=project_id, agent_role="Backlog", event="sprint.item_removed",
                           human_reviewer=user.email, detail={"key": row["item_key"]})
        return item_view(new)

    async def _after_write(self, project_id: str, row: Any, op: str) -> None:
        if self.sync_hook is not None:
            try:
                await self.sync_hook(project_id, row, op)
            except Exception:
                import logging
                logging.getLogger("agile").warning("jira write-through failed for %s", row["item_key"], exc_info=True)
