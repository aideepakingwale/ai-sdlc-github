"""Jira ⇄ backlog synchronisation with explicit FIELD OWNERSHIP (never a free-for-all two-way merge).

* Jira owns what people edit there: summary, description, acceptance criteria, story points, labels, epic link, and
  done / in-progress status.
* DevMind owns: its item key, rank, components and the sprint commitment (a Jira sprint is informational only).
* DevMind-created items are pushed to Jira once; after that Jira owns the content, and later DevMind edits are written
  through with optimistic concurrency (a lost race means Jira wins and the item is re-pulled).
* Inbound sync is incremental by a watermark with an overlap window, de-duplicated per item by Jira's `updated`, so a
  time-zone offset or clock skew can only cause harmless re-reads, never missed changes.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any

from ..domain.errors import SdlcError
from .rules import clean_criteria, validate_estimate

log = logging.getLogger("agile")

OVERLAP = dt.timedelta(hours=26)       # > the largest UTC offset + a day of slack
PAGE_SIZE = 100
MAX_PAGES = 50                          # per run; the watermark carries the rest to the next run
TYPE_MAP = {"epic": "epic", "story": "story", "bug": "bug", "task": "task", "sub-task": "task", "subtask": "task",
            "improvement": "story", "new feature": "story", "technical task": "task"}
OUT_STATUS = {"in_progress": "In Progress", "done": "Done", "ready": "To Do", "new": "To Do", "refined": "To Do",
              "in_sprint": "To Do"}


# ------------------------------------------------------------------ pure rules
def map_type(jira_type: str) -> str | None:
    return TYPE_MAP.get((jira_type or "").strip().lower())


def inbound_status(category: str, current: str | None) -> str:
    """DevMind status after reading a Jira issue. Jira only moves an item to done / in_progress (and back when
    reopened); planning statuses (new/refined/ready/in_sprint) stay under DevMind's control."""
    if category == "done":
        return "done"
    if category == "inprogress":
        return "in_progress" if current in ("in_sprint", "in_progress") else (current or "new")
    # todo
    if current == "done":
        return "ready"
    if current == "in_progress":
        return "in_sprint"
    return current or "new"


def issue_fields(issue: dict[str, Any]) -> dict[str, Any]:
    """The Jira-owned columns for a backlog row, validated; invalid values are dropped, not guessed."""
    out: dict[str, Any] = {
        "title": " ".join((issue.get("summary") or "").split())[:200] or issue["key"],
        "description": (issue.get("description") or "")[:8000],
        "acceptance_criteria": clean_criteria(issue.get("acceptanceCriteria") or []),
        "labels": [str(x)[:40] for x in (issue.get("labels") or [])][:20],
    }
    try:
        out["estimate"] = validate_estimate(issue.get("storyPoints"))
    except ValueError:
        out["estimate"] = None
    return out


def parse_ts(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        t = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


def needs_pull(item: Any, issue: dict[str, Any]) -> bool:
    """Skip an issue we already have at (or beyond) this Jira revision."""
    remote, known = parse_ts(issue.get("updated")), item["jira_updated"]
    return remote is None or known is None or remote > known


def changed_locally_since_sync(item: Any) -> bool:
    synced = item["jira_synced_at"]
    return synced is not None and item["updated_at"] > synced + dt.timedelta(seconds=2)


@dataclass
class SyncResult:
    status: str = "ok"
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    ignored: int = 0
    conflicts: int = 0
    pushed: int = 0
    errors: list[str] = field(default_factory=list)
    pages: int = 0
    watermark: str | None = None

    def view(self) -> dict[str, Any]:
        return {"status": self.status, "created": self.created, "updated": self.updated, "unchanged": self.unchanged,
                "ignored": self.ignored, "conflicts": self.conflicts, "pushed": self.pushed,
                "errors": self.errors[:10], "pages": self.pages, "watermark": self.watermark}


# ------------------------------------------------------------------ service
class JiraSyncService:
    def __init__(self, db: Any, mcp: Any, audit: Any, agile: Any, locks: Any = None) -> None:
        self._db, self._mcp, self._audit, self._agile = db, mcp, audit, agile
        self._locks = locks or _Locks()

    async def _project_key(self, project_id: str) -> str | None:
        p = await self._db.get_project(project_id)
        return (p.get("jira_project_key") or None) if p else None

    async def status(self, project_id: str, user: Any) -> dict[str, Any]:
        await self._agile._authz.assert_project_access(project_id, user)
        key = await self._project_key(project_id)
        row = await self._db.get_sync_state(project_id)
        base: dict[str, Any] = {"enabled": bool(key), "projectKey": key}
        if not key:
            base["reason"] = "No Jira project key is configured for this project (set it in the project integrations)."
        if row:
            base.update({"lastRunAt": row["last_run_at"].isoformat() if row["last_run_at"] else None,
                         "lastStatus": row["last_status"], "lastError": row["last_error"],
                         "watermark": row["watermark"].isoformat() if row["watermark"] else None,
                         "stats": row["stats"] or {}})
        return base

    # ------------------------------------------------------------------ inbound
    async def sync(self, project_id: str, user: Any | None = None, *, full: bool = False) -> dict[str, Any]:
        if user is not None:
            await self._agile.assert_can_run(project_id, user)
        key = await self._project_key(project_id)
        if not key:
            raise SdlcError("VALIDATION_FAILED", "No Jira project key is configured for this project")
        async with self._locks(project_id):
            res = await self._run(project_id, key, full=full)
        self._audit.record(project_id=project_id, agent_role="JiraSync", event="jira.synced",
                           human_reviewer=getattr(user, "email", None), detail=res.view())
        return res.view()

    async def _run(self, project_id: str, key: str, *, full: bool) -> SyncResult:
        res = SyncResult()
        state = await self._db.get_sync_state(project_id)
        mark: dt.datetime | None = None if full or not state else state["watermark"]
        since = (mark - OVERLAP).isoformat() if mark else None
        newest = mark
        failed_ts: list[dt.datetime | None] = []           # `updated` of issues that failed to apply this run
        token: str | None = None
        epic_ids: dict[str, str] = {}                     # jira key -> backlog id, for epic links
        pending_links: list[tuple[str, str]] = []         # (backlog id, epic jira key) resolved after the pass
        try:
            for _ in range(MAX_PAGES):
                args: dict[str, Any] = {"projectKey": key, "maxResults": PAGE_SIZE}
                if since:
                    args["updatedSince"] = since
                if token:
                    args["nextPageToken"] = token
                page = await self._mcp.call("jira_search_issues", args)
                res.pages += 1
                for issue in page.get("issues", []):
                    try:
                        row = await self._apply_issue(project_id, issue, res)
                    except Exception as err:
                        res.errors.append(f"{issue.get('key')}: {str(err)[:160]}")
                        failed_ts.append(parse_ts(issue.get("updated")))
                        continue
                    if row is not None:
                        if row["type"] == "epic":
                            epic_ids[issue["key"]] = row["id"]
                        if issue.get("epicKey") and row["type"] != "epic":
                            pending_links.append((row["id"], issue["epicKey"]))
                    ts = parse_ts(issue.get("updated"))
                    if ts and (newest is None or ts > newest):
                        newest = ts
                token = page.get("nextPageToken")
                if not token:
                    break
            await self._link_epics(project_id, epic_ids, pending_links)
            if failed_ts:                                   # a failed issue must be re-read next run: never move past it
                if any(t is None for t in failed_ts):
                    newest = mark
                else:
                    cap = min(t for t in failed_ts if t is not None) - dt.timedelta(seconds=1)
                    newest = cap if newest is None or cap < newest else newest
                    if mark is not None and newest < mark:
                        newest = mark
            res.watermark = newest.isoformat() if newest else None
            res.status = "partial" if res.errors else "ok"
        except Exception as err:
            res.status, res.watermark = "error", (mark.isoformat() if mark else None)
            res.errors.append(str(err)[:300])
            await self._db.save_sync_state(project_id, watermark=None, status="error", error=str(err)[:500], stats=res.view())
            return res
        await self._db.save_sync_state(project_id, watermark=newest, status=res.status,
                                       error="; ".join(res.errors[:3]) or None, stats=res.view())
        return res

    async def _apply_issue(self, project_id: str, issue: dict[str, Any], res: SyncResult) -> Any | None:
        if not isinstance(issue.get("key"), str) or not issue["key"].strip():
            raise ValueError("issue has no key")        # never create an item that cannot be linked back to Jira
        typ = map_type(issue.get("type", ""))
        if typ is None:
            res.ignored += 1
            return None
        item = await self._db.get_backlog_by_jira(project_id, issue["key"])
        remote = parse_ts(issue.get("updated"))
        f = issue_fields(issue)
        if typ == "epic":
            f.pop("estimate", None)
        cat = issue.get("statusCategory", "unknown")
        if item is None:
            item = await self._adoptable(project_id, typ, f["title"])
            if item is not None:                           # a local item that was never pushed: link, don't duplicate
                row = await self._db.update_backlog_item(
                    project_id, item["id"], expected_version=None, jira_key=issue["key"], jira_updated=remote,
                    jira_synced_at=dt.datetime.now(dt.UTC), **self._keep_local(item, f))
                res.updated += 1
                return row
        if item is None:
            status = "done" if cat == "done" else ("refined" if f["acceptance_criteria"] or typ == "epic" else "new")
            row = await self._db.insert_backlog_item(
                project_id=project_id, created_by=None, type=typ, status=status, jira_key=issue["key"],
                jira_updated=remote, jira_synced_at=dt.datetime.now(dt.UTC), **f)
            res.created += 1
            return row
        if not needs_pull(item, issue):
            res.unchanged += 1
            return item
        if changed_locally_since_sync(item):
            res.conflicts += 1                            # Jira wins for Jira-owned fields; audited via the run summary
        fields = self._keep_local(item, f)
        new_status = inbound_status(cat, item["status"])
        if new_status != item["status"] and item["status"] != "dropped":
            fields["status"] = new_status
            if new_status == "ready" and item["iteration_id"]:
                fields["iteration_id"] = None
        row = await self._db.update_backlog_item(
            project_id, item["id"], expected_version=None, jira_updated=remote,
            jira_synced_at=dt.datetime.now(dt.UTC), **fields)
        res.updated += 1
        return row

    @staticmethod
    def _keep_local(item: Any, f: dict[str, Any]) -> dict[str, Any]:
        """Jira owns these fields, but an EMPTY Jira value must not wipe a value a person entered here."""
        out = dict(f)
        if not out.get("acceptance_criteria") and item["acceptance_criteria"]:
            out.pop("acceptance_criteria", None)
        if out.get("estimate") is None and item["estimate"] is not None:
            out.pop("estimate", None)
        return out

    async def _adoptable(self, project_id: str, typ: str, title: str) -> Any | None:
        want = " ".join(title.lower().split())
        for r in await self._db.list_backlog(project_id):
            if not r["jira_key"] and r["type"] == typ and r["status"] != "dropped" \
                    and " ".join(r["title"].lower().split()) == want:
                return r
        return None

    async def _link_epics(self, project_id: str, epic_ids: dict[str, str], links: list[tuple[str, str]]) -> None:
        for backlog_id, epic_key in links:
            epic_id = epic_ids.get(epic_key)
            if epic_id is None:
                e = await self._db.get_backlog_by_jira(project_id, epic_key)
                epic_id = e["id"] if e and e["type"] == "epic" else None
            if epic_id:
                await self._db.update_backlog_item(project_id, backlog_id, expected_version=None, epic_id=epic_id)

    # ------------------------------------------------------------------ outbound (write-through)
    async def write_through(self, project_id: str, row: Any, op: str) -> None:
        """Called after a backlog write. No Jira project key → nothing to do."""
        key = await self._project_key(project_id)
        if not key:
            return
        if row["jira_key"]:
            await self._push_update(project_id, key, row, op)
        else:
            await self._push_create(project_id, key, row)

    async def _push_create(self, project_id: str, key: str, row: Any) -> None:
        async with self._locks(project_id):               # a concurrent sync must not import our own create as a twin
            fresh = await self._db.get_backlog_item(project_id, row["id"])
            if fresh is None or fresh["jira_key"]:
                return
            await self._push_create_locked(project_id, key, fresh)

    async def _push_create_locked(self, project_id: str, key: str, row: Any) -> None:
        if row["status"] == "dropped":
            return
        if row["type"] == "epic":
            out = await self._mcp.call("jira_create_epic", {
                "title": row["title"], "description": row["description"] or row["title"], "priority": "Medium",
                "projectKey": key})
            jira_key = out["epicKey"]
        else:
            if not row["acceptance_criteria"]:
                return                                    # the connector requires acceptance criteria; push once ready
            epic = await self._db.get_backlog_item(project_id, row["epic_id"]) if row["epic_id"] else None
            if epic is None or not epic["jira_key"]:
                return                                    # needs an epic that exists in Jira first; pushed on a later edit/sync
            args: dict[str, Any] = {"epicKey": epic["jira_key"], "storyText": row["title"],
                                    "gherkinCriteria": list(row["acceptance_criteria"]), "projectKey": key}
            if row["estimate"] is not None and float(row["estimate"]) > 0:
                args["storyPoints"] = max(1, int(round(float(row["estimate"]))))
            out = await self._mcp.call("jira_create_story", args)
            jira_key = out["storyKey"]
        got = await self._mcp.call("jira_get_issue", {"key": jira_key})
        await self._db.update_backlog_item(
            project_id, row["id"], expected_version=None, jira_key=jira_key,
            jira_updated=parse_ts(got["issue"]["updated"]), jira_synced_at=dt.datetime.now(dt.UTC))

    async def _push_update(self, project_id: str, key: str, row: Any, op: str) -> None:
        item_updated = row["jira_updated"]
        try:
            if op in ("update", "create"):
                fields: dict[str, Any] = {"summary": row["title"], "description": row["description"] or ""}
                if row["acceptance_criteria"]:
                    fields["acceptanceCriteria"] = list(row["acceptance_criteria"])
                if row["estimate"] is not None:
                    fields["storyPoints"] = float(row["estimate"])
                fields["labels"] = list(row["labels"] or [])
                args: dict[str, Any] = {"key": row["jira_key"], "fields": fields}
                if item_updated is not None:
                    args["expectedUpdated"] = item_updated.isoformat()
                out = await self._mcp.call("jira_update_issue", args)
                item_updated = parse_ts(out["issue"]["updated"])
            if op == "status" and row["status"] in OUT_STATUS:
                out = await self._mcp.call("jira_transition_issue", {"key": row["jira_key"], "toStatus": OUT_STATUS[row["status"]]})
                item_updated = parse_ts(out["issue"]["updated"])
        except SdlcError as err:
            if err.code == "GATE_CONFLICT":
                # Someone changed it in Jira meanwhile: Jira owns the content, so pull instead of overwriting.
                await self.sync(project_id)
                return
            raise
        await self._db.update_backlog_item(project_id, row["id"], expected_version=None, jira_updated=item_updated,
                                           jira_synced_at=dt.datetime.now(dt.UTC))

    async def sync_all(self) -> int:
        """Background tick: sync every project that has Agile + a Jira key. Returns projects processed."""
        n = 0
        for pid in await self._db.list_agile_project_ids():
            try:
                await self.sync(pid)
                n += 1
            except Exception:
                log.warning("scheduled Jira sync failed project=%s", pid, exc_info=True)
        return n


class _Locks:
    def __init__(self) -> None:
        import asyncio
        self._m: dict[str, asyncio.Lock] = {}
        self._asyncio = asyncio

    def __call__(self, project_id: str) -> Any:
        return self._m.setdefault(project_id, self._asyncio.Lock())
