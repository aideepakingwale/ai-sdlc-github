"""Token usage reports over the ledger (docs/token-governance.md, phase 1).

Who sees what:
  * a project's manager and the administrators see every person's figures in that project;
  * any other member sees the project total and their own line;
  * a person's figures are only ever read inside a project (there is no cross-project personal total);
  * the organisation view is for administrators and stops at the project level.
Amounts are tokens: input (uncached), cache read, cache write and output, summed as `tokens`.
"""
from __future__ import annotations

import csv
import io
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..domain.errors import SdlcError
from ..domain.models import UserPublic
from ..repos.usage_repo import UsageFilter, UsageRepo

MAX_DAYS = 400
BILLABLE_MODES = ("billable", "offline", "all")
CSV_ROW_CAP = 50_000


def parse_range(frm: str | None, to: str | None, today: date | None = None) -> tuple[date, date]:
    """Inclusive day range in UTC; defaults to the last 30 days."""
    end = _day(to, "to") if to else (today or datetime.now(timezone.utc).date())
    start = _day(frm, "from") if frm else end - timedelta(days=29)
    if start > end:
        raise SdlcError("VALIDATION_FAILED", "'from' must not be after 'to'")
    if (end - start).days + 1 > MAX_DAYS:
        raise SdlcError("VALIDATION_FAILED", f"Choose at most {MAX_DAYS} days")
    return start, end


def _day(v: str, name: str) -> date:
    try:
        return date.fromisoformat(v[:10])
    except ValueError as err:
        raise SdlcError("VALIDATION_FAILED", f"'{name}' must be a date like 2026-10-31") from err


def _flt(start: date, end: date, billable: str, project_id: str | None = None, user_id: str | None = None) -> UsageFilter:
    if billable not in BILLABLE_MODES:
        raise SdlcError("VALIDATION_FAILED", f"billable must be one of {', '.join(BILLABLE_MODES)}")
    since = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    until = datetime(end.year, end.month, end.day, tzinfo=timezone.utc) + timedelta(days=1)
    return UsageFilter(since=since, until=until, project_id=project_id, user_id=user_id, billable=billable)


def _share(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


def _totals(t: dict[str, Any]) -> dict[str, Any]:
    prompt = t["input_tokens"] + t["cache_read_tokens"] + t["cache_write_tokens"]
    return {
        "tokens": t["tokens"], "calls": t["calls"], "inputTokens": t["input_tokens"], "cacheReadTokens": t["cache_read_tokens"],
        "cacheWriteTokens": t["cache_write_tokens"], "outputTokens": t["output_tokens"],
        "cacheReadShare": _share(t["cache_read_tokens"], prompt),
        "wasteTokens": t["waste_tokens"], "wasteShare": _share(t["waste_tokens"], t["tokens"]),
        "estimatedTokens": t["estimated_tokens"],
    }


def _who(r: dict[str, Any]) -> str:
    return r.get("display_name") or r.get("email") or ("Unknown person" if r.get("user_id") else "No person recorded")


def _csv_cell(v: Any) -> Any:
    """Stop a spreadsheet from running a cell as a formula."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


log = logging.getLogger("usage-reports")


class UsageReports:
    def __init__(self, repo: UsageRepo, authz: Any, captures: Any = None, capture_days: int = 0) -> None:
        self._repo = repo
        self._authz = authz
        self._captures = captures          # CaptureRepo, or None when failed-call capture is not wired
        self._capture_days = capture_days  # 0 = capture is off

    # ------------------------------------------------------------------ a project
    async def project(self, user: UserPublic, project_id: str, frm: str | None, to: str | None, billable: str = "billable", person: str | None = None) -> dict[str, Any]:
        await self._authz.assert_project_access(project_id, user)
        manager = await self._authz.is_project_manager(project_id, user)
        start, end = parse_range(frm, to)
        who = self._who_filter(user, manager, person)
        whole = _flt(start, end, billable, project_id)
        mine = _flt(start, end, billable, project_id, user.id)
        sel = _flt(start, end, billable, project_id, who) if who else whole
        totals = _totals(await self._repo.totals(sel))
        out: dict[str, Any] = {
            "range": {"from": start.isoformat(), "to": end.isoformat(), "days": (end - start).days + 1},
            "project": {"id": project_id, "name": await self._repo.project_name(project_id)},
            "billable": billable,
            "viewer": {"id": user.id, "canSeeEveryone": manager},
            "person": who,
            "totals": totals,
            "projectTotal": _totals(await self._repo.totals(whole)) if who else totals,
            "you": _totals(await self._repo.totals(mine)),
            "byDay": [{"day": r["day"].isoformat(), "tokens": r["tokens"], "calls": r["calls"]} for r in await self._repo.by_day(sel)],
            "byActivity": [{"activity": r["activity"], "tokens": r["tokens"], "calls": r["calls"], "wasteTokens": r["waste_tokens"]} for r in await self._repo.by_activity(sel)],
            "byStage": [{"stage": r["stage"], "tokens": r["tokens"], "calls": r["calls"]} for r in await self._repo.by_stage(sel)],
            "byModel": [{"provider": r["provider"], "model": r["model"], "tokens": r["tokens"], "calls": r["calls"]} for r in await self._repo.by_model(sel)],
            "byAgent": [{"agentId": r["agent_id"], "name": r["name"] or "Removed agent", "tokens": r["tokens"], "calls": r["calls"]} for r in await self._repo.by_agent(sel)],
            "waste": [{"activity": r["activity"], "status": r["status"], "calls": r["calls"], "tokens": r["tokens"]} for r in await self._repo.waste(sel)],
        }
        people = await self._repo.by_user(whole)
        grand = sum(p["tokens"] for p in people)
        rows = [{"userId": p["user_id"], "name": _who(p), "tokens": p["tokens"], "calls": p["calls"], "share": _share(p["tokens"], grand),
                 "wasteTokens": p["waste_tokens"], "you": p["user_id"] == user.id} for p in people]
        out["people"] = rows if manager else [r for r in rows if r["you"]]
        return out

    def _who_filter(self, user: UserPublic, manager: bool, person: str | None) -> str | None:
        if not person:
            return None
        pid = user.id if person == "me" else person
        if pid != user.id and not manager:
            raise SdlcError("FORBIDDEN", "You can see your own usage and the project total; the project's manager sees everyone's")
        return pid

    async def events(self, user: UserPublic, project_id: str, frm: str | None, to: str | None, billable: str, person: str | None,
                     activity: str | None, status: str | None, limit: int, offset: int) -> dict[str, Any]:
        """The ledger rows behind a number: managers see everyone's, a member sees their own."""
        await self._authz.assert_project_access(project_id, user)
        manager = await self._authz.is_project_manager(project_id, user)
        start, end = parse_range(frm, to)
        who = self._who_filter(user, manager, person) or (None if manager else user.id)
        rows = await self._repo.events(_flt(start, end, billable, project_id, who), activity=activity, status=status,
                                       limit=max(1, min(limit, 500)), offset=max(0, offset))
        events = [self._event(r) for r in rows]
        if not manager:                     # the text of a request is for the project's manager and administrators only
            for e in events:
                e["hasCapture"] = False
        return {"events": events, "limit": limit, "offset": offset}

    async def request_detail(self, user: UserPublic, project_id: str, request_id: str) -> dict[str, Any]:
        """One request, attempt by attempt, with what was sent and what came back when it was captured (docs/token-governance.md, section 20)."""
        await self._authz.assert_project_access(project_id, user)
        if not await self._authz.is_project_manager(project_id, user):
            raise SdlcError("FORBIDDEN", "The prompt and response of a request can contain the project's documents: the project's manager and administrators can open them")
        rows = await self._repo.request_rows(project_id, request_id)
        cap = await self._captures.get(project_id, request_id) if self._captures is not None else None
        if not rows and not cap:
            raise SdlcError("NOT_FOUND", "No such request in this project")
        said = (cap or {}).get("attempts") or []
        attempts = []
        for r in rows:
            e = self._event(r)
            note = said[r["attempt_no"] - 1] if 0 < r["attempt_no"] <= len(said) else {}
            attempts.append({**{k: e[k] for k in ("attempt", "provider", "model", "region", "providerRequestId", "status", "errorClass", "stopReason", "latencyMs",
                                                    "inputTokens", "cacheReadTokens", "cacheWriteTokens", "outputTokens", "totalTokens", "estimated", "billable")},
                             "error": note.get("error"), "errorKind": note.get("errorKind")})
        first = rows[0] if rows else None
        out: dict[str, Any] = {
            "requestId": request_id, "ts": first["ts"].isoformat() if first else cap["created_at"].isoformat(),
            "person": _who(first) if first else None, "activity": first["activity"] if first else cap["activity"], "stage": first["stage"] if first else cap["stage"],
            "tag": first["tag"] if first else cap["tag"], "totalTokens": sum(a["totalTokens"] for a in attempts), "attempts": attempts, "capture": None,
        }
        if cap:
            out["capture"] = {
                "reason": cap["reason"], "error": cap["error"], "capturedAt": cap["created_at"].isoformat(),
                "expiresAt": (cap["created_at"] + timedelta(days=self._capture_days)).isoformat() if self._capture_days else None,
                **self._prompt(cap["request_body"]), "promptCut": cap["request_cut"],
                "response": cap["response_body"], "responseCut": cap["response_cut"],
            }
        else:
            out["captureNote"] = self._why_not(first["ts"] if first else None)
        log.info("request %s of project %s opened by %s (%s)", request_id, project_id, user.id, "captured" if cap else "no capture")
        return out

    @staticmethod
    def _prompt(body: str | None) -> dict[str, Any]:
        """The messages sent, as a list when the stored text is whole JSON; the raw text when it was cut off."""
        if not body:
            return {"messages": None, "promptText": None}
        try:
            msgs = json.loads(body)
            if isinstance(msgs, list):
                return {"messages": [{"role": str(m.get("role", "")), "content": m["content"] if isinstance(m.get("content"), str) else json.dumps(m.get("content"), ensure_ascii=False, indent=2)}
                                     for m in msgs if isinstance(m, dict)], "promptText": None}
        except (ValueError, TypeError, KeyError):
            pass
        return {"messages": None, "promptText": body}

    def _why_not(self, ts: datetime | None) -> str:
        if self._captures is None or not self._capture_days:
            return "This server does not keep the text of failed requests."
        if ts is not None and datetime.now(timezone.utc) - ts > timedelta(days=self._capture_days):
            return f"The text of a failed request is kept for {self._capture_days} days and has been deleted."
        return "No text was kept for this request: it finished cleanly, or it ran before failed requests were captured."

    @staticmethod
    def _event(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "ts": r["ts"].isoformat(), "requestId": r["request_id"], "attempt": r["attempt_no"], "provider": r["provider"], "model": r["model"],
            "region": r["region"], "providerRequestId": r["provider_request_id"], "stage": r["stage"], "userId": r["user_id"], "person": _who(r),
            "actorType": r["actor_type"], "activity": r["activity"], "tag": r["tag"], "agentId": r["agent_id"], "runId": r["run_id"],
            "inputTokens": r["input_tokens"], "cacheReadTokens": r["cache_read_tokens"], "cacheWriteTokens": r["cache_write_tokens"],
            "outputTokens": r["output_tokens"], "totalTokens": r["total_tokens"], "status": r["status"], "errorClass": r["error_class"],
            "stopReason": r["stop_reason"], "latencyMs": r["latency_ms"], "estimated": r["estimated"], "billable": r["billable"],
            "hasCapture": bool(r.get("has_capture")),
        }

    async def project_csv(self, user: UserPublic, project_id: str, frm: str | None, to: str | None, billable: str, person: str | None) -> str:
        await self._authz.assert_project_access(project_id, user)
        manager = await self._authz.is_project_manager(project_id, user)
        start, end = parse_range(frm, to)
        who = self._who_filter(user, manager, person) or (None if manager else user.id)
        f = _flt(start, end, billable, project_id, who)
        cols = ["ts", "person", "activity", "stage", "agentId", "tag", "provider", "model", "status", "inputTokens", "cacheReadTokens",
                "cacheWriteTokens", "outputTokens", "totalTokens", "estimated", "billable", "requestId", "attempt", "providerRequestId", "stopReason", "latencyMs"]
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        # One query, one snapshot (paging with OFFSET while rows are being written can skip or repeat rows). One extra row tells us whether the cap cut it off.
        rows = await self._repo.events(f, activity=None, status=None, limit=CSV_ROW_CAP + 1, offset=0)
        for r in rows[:CSV_ROW_CAP]:
            e = self._event(r)
            w.writerow([_csv_cell(e[c]) for c in cols])
        if len(rows) > CSV_ROW_CAP:
            w.writerow([f"TRUNCATED: more than {CSV_ROW_CAP} rows in this selection; the newest {CSV_ROW_CAP} are shown. Choose a shorter period or one person."])
        return buf.getvalue()

    # ------------------------------------------------------------------ the organisation
    async def organisation(self, user: UserPublic, frm: str | None, to: str | None, billable: str = "billable") -> dict[str, Any]:
        self._require_admin(user)
        start, end = parse_range(frm, to)
        f = _flt(start, end, billable)
        projects = await self._repo.by_project(f)
        return {
            "range": {"from": start.isoformat(), "to": end.isoformat(), "days": (end - start).days + 1},
            "billable": billable,
            "totals": _totals(await self._repo.totals(f)),
            "byDay": [{"day": r["day"].isoformat(), "tokens": r["tokens"], "calls": r["calls"]} for r in await self._repo.by_day(f)],
            "byProject": [{"projectId": p["project_id"], "name": p["name"] or f"Deleted project ({str(p['project_id'])[:8]})", "deleted": p["name"] is None, "tokens": p["tokens"], "calls": p["calls"], "people": p["people"],
                           "wasteTokens": p["waste_tokens"], "wasteShare": _share(p["waste_tokens"], p["tokens"])} for p in projects],
            "byActivity": [{"activity": r["activity"], "tokens": r["tokens"], "calls": r["calls"], "wasteTokens": r["waste_tokens"]} for r in await self._repo.by_activity(f)],
            "byModel": [{"provider": r["provider"], "model": r["model"], "tokens": r["tokens"], "calls": r["calls"]} for r in await self._repo.by_model(f)],
            "waste": [{"activity": r["activity"], "status": r["status"], "calls": r["calls"], "tokens": r["tokens"]} for r in await self._repo.waste(f)],
            "unattributed": await self._repo.unattributed(f),
        }

    async def organisation_csv(self, user: UserPublic, frm: str | None, to: str | None, billable: str) -> str:
        data = await self.organisation(user, frm, to, billable)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["project", "tokens", "calls", "people", "wasteTokens", "wasteShare"])
        for p in data["byProject"]:
            w.writerow([_csv_cell(p["name"]), p["tokens"], p["calls"], p["people"], p["wasteTokens"], p["wasteShare"]])
        return buf.getvalue()

    @staticmethod
    def _require_admin(user: UserPublic) -> None:
        if user.role != "SUPER_ADMIN":
            raise SdlcError("FORBIDDEN", "The organisation's usage is for administrators; open a project to see its own")
