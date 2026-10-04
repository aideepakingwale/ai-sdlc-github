"""AgileService — lifecycle of sprints and releases on top of the workflow engine.

It never decides *what* a stage produces (agents and gates do that); it owns the structure: enabling a
methodology, materialising a sprint's stage slots, closing a sprint when its closing stage is approved,
releases, and the read model the UI renders. Every state change is audited, authorised and idempotent."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Awaitable, Callable

import asyncpg

from ..domain.errors import SdlcError
from ..domain.models import UserPublic
from .engine import (
    InstanceRef, allocate_seqs, instance_key, iteration_block,
)
from .templates import template_for

log = logging.getLogger("agile")

Hook = Callable[["StageCtx"], Awaitable[None]]


class StageCtx:
    """What a lifecycle hook needs to know about the stage that just changed."""

    def __init__(self, project_id: str, stage: dict[str, Any], iteration: dict[str, Any] | None,
                 release: dict[str, Any] | None, actor: str) -> None:
        self.project_id, self.stage, self.iteration, self.release, self.actor = (
            project_id, stage, iteration, release, actor)

    @property
    def role(self) -> str | None:
        return self.stage.get("agileRole")

    @property
    def hook_names(self) -> list[str]:
        """Hooks registered for this stage's agile role and/or its base stage key ("key:vision")."""
        base = self.stage.get("baseKey") or self.stage.get("key")
        return [n for n in (self.role, f"key:{base}" if base else None) if n]

    @property
    def phase(self) -> int:
        return self.stage["seq"]


class AgileService:
    def __init__(self, db: Any, dynamo: Any, workflow: Any, audit: Any, authz: Any) -> None:
        self._db, self._dynamo, self._workflow, self._audit, self._authz = db, dynamo, workflow, audit, authz
        # role -> hooks. Registered by the backlog/proposal and index modules; kept as plain lists so the
        # lifecycle stays testable on its own.
        self._on_generated: dict[str, list[Hook]] = {}
        self._on_approved: dict[str, list[Hook]] = {}

    def on_generated(self, role: str, fn: Hook) -> None:
        self._on_generated.setdefault(role, []).append(fn)

    def on_approved(self, role: str, fn: Hook) -> None:
        self._on_approved.setdefault(role, []).append(fn)

    # ------------------------------------------------------------------ authorisation
    async def _is_manager(self, project_id: str, user: UserPublic) -> bool:
        if user.role == "SUPER_ADMIN":
            return True
        project = await self._db.get_project(project_id)
        return bool(project) and user.role == "PROJECT_MANAGER" and project["created_by"] == user.id

    async def _assert_manager(self, project_id: str, user: UserPublic) -> None:
        if not await self._is_manager(project_id, user):
            raise SdlcError("FORBIDDEN", "Only the managing Project Manager (or an admin) can change this")

    async def assert_can_run(self, project_id: str, user: UserPublic) -> None:
        """Run the sprint ceremonies: the managing PM, an admin, or a Product Owner on the project."""
        if await self._is_manager(project_id, user):
            return
        if await self._authz.get_membership_role(project_id, user.id) == "PO":
            return
        raise SdlcError("FORBIDDEN", "Only the Product Owner or the managing Project Manager can do this")

    async def _settings(self, project_id: str) -> Any:
        row = await self._db.get_project_agile(project_id)
        if not row:
            raise SdlcError("VALIDATION_FAILED", "This project does not use an Agile methodology. Enable Scrum or Kanban first.")
        return row

    # ------------------------------------------------------------------ enabling
    async def enable(
        self, project_id: str, user: UserPublic, *, methodology: str, sprint_days: int = 14,
        default_capacity: float = 30.0, wip_limit: int | None = None,
        index_strategy: str = "index-branch", auto_min_score: int = 80,
    ) -> dict[str, Any]:
        await self._assert_manager(project_id, user)
        if methodology not in ("scrum", "kanban"):
            raise SdlcError("VALIDATION_FAILED", "methodology must be 'scrum' or 'kanban'")
        if not 1 <= sprint_days <= 42:
            raise SdlcError("VALIDATION_FAILED", "sprintDays must be between 1 and 42")
        if default_capacity < 0:
            raise SdlcError("VALIDATION_FAILED", "capacity cannot be negative")
        if wip_limit is not None and not 1 <= wip_limit <= 100:
            raise SdlcError("VALIDATION_FAILED", "wipLimit must be between 1 and 100")
        if index_strategy not in ("index-branch", "default-branch"):
            raise SdlcError("VALIDATION_FAILED", "indexStrategy must be 'index-branch' or 'default-branch'")
        if not 0 <= auto_min_score <= 100:
            raise SdlcError("VALIDATION_FAILED", "autoMinScore must be between 0 and 100")
        if await self._db.get_project_agile(project_id):
            raise SdlcError("GATE_CONFLICT", "An Agile methodology is already enabled on this project")
        started = [s for s in await self._dynamo.list_phase_states(project_id)
                   if s.get("status") not in (None, "NOT_STARTED")]
        if started:
            raise SdlcError(
                "GATE_CONFLICT",
                "This project has already started a waterfall workflow; create a new project for Agile delivery")
        await self._workflow.save(project_id, template_for(methodology).model_dump(), user)
        await self._db.insert_project_agile(
            project_id=project_id, methodology=methodology, sprint_days=sprint_days,
            default_capacity=default_capacity, wip_limit=wip_limit, index_strategy=index_strategy,
            auto_min_score=auto_min_score, created_by=user.id)
        rel = await self._db.insert_release(project_id=project_id, name="Release 1")
        self._audit.record(
            project_id=project_id, agent_role="Agile", event="agile.enabled", human_reviewer=user.email,
            detail={"methodology": methodology, "sprintDays": sprint_days, "capacity": default_capacity,
                    "indexStrategy": index_strategy, "release": rel["code"]})
        return await self.overview(project_id, user)

    async def update_settings(self, project_id: str, user: UserPublic, changes: dict[str, Any]) -> dict[str, Any]:
        await self._assert_manager(project_id, user)
        await self._settings(project_id)
        allowed = {"sprint_days": (1, 42), "default_capacity": (0, 10_000), "wip_limit": (1, 100),
                   "auto_min_score": (0, 100)}
        clean: dict[str, Any] = {}
        for k, v in changes.items():
            if k == "index_strategy":
                if v not in ("index-branch", "default-branch"):
                    raise SdlcError("VALIDATION_FAILED", "indexStrategy must be 'index-branch' or 'default-branch'")
                clean[k] = v
            elif k in allowed:
                if v is None and k == "wip_limit":
                    clean[k] = None
                    continue
                lo, hi = allowed[k]
                if not isinstance(v, (int, float)) or isinstance(v, bool) or not lo <= v <= hi:
                    raise SdlcError("VALIDATION_FAILED", f"{k} must be between {lo} and {hi}")
                clean[k] = v
            else:
                raise SdlcError("VALIDATION_FAILED", f"'{k}' cannot be changed")
        await self._db.update_project_agile(project_id, **clean)
        self._audit.record(project_id=project_id, agent_role="Agile", event="agile.settings_updated",
                           human_reviewer=user.email, detail=clean)
        return await self.overview(project_id, user)

    # ------------------------------------------------------------------ read model
    async def overview(self, project_id: str, user: UserPublic) -> dict[str, Any]:
        await self._authz.assert_project_access(project_id, user)
        cfg = await self._db.get_project_agile(project_id)
        manager = await self._is_manager(project_id, user)
        if not cfg:
            return {"enabled": False, "methodology": "waterfall", "permissions": {"canManage": manager, "canRun": manager}}
        releases = await self._db.list_releases(project_id)
        iterations = await self._db.list_iterations(project_id)
        counts = await self._db.count_backlog_by_status(project_id)
        open_it = next((i for i in iterations if i["status"] in ("planned", "active")), None)
        open_rel = next((r for r in releases if r["status"] in ("open", "hardening")), None)
        velocity = [{"sprint": i["label"], "points": (i["summary"] or {}).get("velocity", 0),
                     "completed": (i["summary"] or {}).get("completed", 0)}
                    for i in iterations if i["status"] == "closed"]
        can_run = manager
        if not can_run:
            can_run = (await self._authz.get_membership_role(project_id, user.id)) == "PO"
        return {
            "enabled": True, "methodology": cfg["methodology"],
            "permissions": {"canManage": manager, "canRun": can_run},
            "settings": {
                "sprintDays": cfg["sprint_days"], "defaultCapacity": float(cfg["default_capacity"]),
                "wipLimit": cfg["wip_limit"], "indexStrategy": cfg["index_strategy"],
                "autoMinScore": cfg["auto_min_score"],
            },
            "releases": [self._release_view(r) for r in releases],
            "iterations": [self._iteration_view(i) for i in iterations],
            "currentIteration": self._iteration_view(open_it) if open_it else None,
            "currentRelease": self._release_view(open_rel) if open_rel else None,
            "backlog": counts,
            "velocity": velocity,
            "averageVelocity": round(sum(v["points"] for v in velocity[-3:]) / max(len(velocity[-3:]), 1), 1),
        }

    @staticmethod
    def _iteration_view(i: Any) -> dict[str, Any]:
        return {
            "id": i["id"], "number": i["number"], "label": i["label"], "releaseId": i["release_id"],
            "goal": i["goal"], "status": i["status"], "capacity": float(i["capacity"]),
            "startsOn": i["starts_on"].isoformat() if i["starts_on"] else None,
            "endsOn": i["ends_on"].isoformat() if i["ends_on"] else None,
            "summary": i["summary"] or {},
        }

    @staticmethod
    def _release_view(r: Any) -> dict[str, Any]:
        return {"id": r["id"], "number": r["number"], "code": r["code"], "name": r["name"],
                "goal": r["goal"], "status": r["status"]}

    # ------------------------------------------------------------------ sprints
    async def start_sprint(
        self, project_id: str, user: UserPublic, *, goal: str = "", capacity: float | None = None,
        starts_on: dt.date | None = None,
    ) -> dict[str, Any]:
        await self.assert_can_run(project_id, user)
        cfg = await self._settings(project_id)
        await self.reconcile(project_id)       # a stuck close must not block starting the next sprint
        goal = (goal or "").strip()
        if len(goal) > 240:
            raise SdlcError("VALIDATION_FAILED", "The sprint goal must be 240 characters or fewer")
        cap = float(cfg["default_capacity"] if capacity is None else capacity)
        if cap < 0:
            raise SdlcError("VALIDATION_FAILED", "capacity cannot be negative")
        releases = await self._db.list_releases(project_id)
        release = next((r for r in releases if r["status"] == "open"), None)
        if release is None:
            hardening = next((r for r in releases if r["status"] == "hardening"), None)
            raise SdlcError(
                "GATE_CONFLICT",
                f"Release {hardening['code']} is in hardening; close it before starting another sprint"
                if hardening else "There is no open release to add a sprint to")
        base = await self._base_view(project_id)
        block = iteration_block(base["stages"])
        if block is None:
            raise SdlcError("VALIDATION_FAILED", "The workflow has no iteration-scoped stages")
        starts = starts_on or dt.date.today()
        if not dt.date(2000, 1, 1) <= starts <= dt.date(2100, 12, 31):
            raise SdlcError("VALIDATION_FAILED", "startsOn must be a date between 2000 and 2100")
        ends = starts + dt.timedelta(days=int(cfg["sprint_days"]))
        status = "active" if cfg["methodology"] == "kanban" else "planned"

        def slots_for(label: str, number: int, existing: list[Any]) -> list[tuple[int, str, str]]:
            refs = [InstanceRef(e["seq"], e["key"], e["base_key"], e["scope"], e["iteration_id"], e["release_id"])
                    for e in existing]
            alloc = allocate_seqs(base["stages"], refs, scope="iteration", first=number == 1)
            return [(seq, instance_key(k, label), k) for k, seq in alloc.items()]

        try:
            it = await self._db.create_iteration_with_instances(
                project_id=project_id, release_id=release["id"], goal=goal, capacity=cap, status=status,
                starts_on=starts, ends_on=ends, slots_for=slots_for)
        except asyncpg.UniqueViolationError as err:
            raise SdlcError("GATE_CONFLICT", "A sprint is already open. Close it before starting the next one.") from err
        except ValueError as err:
            raise SdlcError("GATE_CONFLICT", f"Cannot start a sprint: {err}") from err
        wf = await self._workflow.view(project_id)
        entry = next((s for s in wf["stages"] if s.get("iterationLabel") == it["label"]
                      and s.get("baseKey") == block.entry), None)
        if entry:
            await self._db.set_project_phase(project_id, entry["seq"], "ACTIVE")
            await self._notify_ready(project_id, [entry])
        self._audit.record(
            project_id=project_id, agent_role="Agile", event="sprint.started", human_reviewer=user.email,
            detail={"sprint": it["label"], "release": release["code"], "capacity": cap, "goal": goal})
        return self._iteration_view(it)

    async def cancel_sprint(self, project_id: str, user: UserPublic, iteration_id: str) -> dict[str, Any]:
        await self.assert_can_run(project_id, user)
        it = await self._db.get_iteration(iteration_id)
        if not it or it["project_id"] != project_id:
            raise SdlcError("NOT_FOUND", "Sprint not found")
        if it["status"] not in ("planned", "active"):
            raise SdlcError("GATE_CONFLICT", f"Sprint {it['label']} is {it['status']} and cannot be cancelled")
        wf = await self._workflow.view(project_id)
        states = {s["SK"]: s for s in await self._dynamo.list_phase_states(project_id)}
        progressed = [s["name"] for s in wf["stages"] if s.get("iterationLabel") == it["label"]
                      and (states.get(f"PHASE#{s['seq']}") or {}).get("status") not in (None, "NOT_STARTED")]
        if progressed:
            raise SdlcError("GATE_CONFLICT",
                            f"Sprint {it['label']} has started work ({', '.join(progressed)}) and cannot be cancelled")
        items = await self._db.list_backlog(project_id, iteration_id=iteration_id)
        started = [i["item_key"] for i in items if i["status"] in ("in_progress", "done")]
        if started:
            raise SdlcError("GATE_CONFLICT",
                            f"Sprint {it['label']} already has started or finished items ({', '.join(started[:5])}); "
                            "close it instead so that work is kept")
        await self._db.detach_items_from_iteration(iteration_id)
        row = await self._db.update_iteration(iteration_id, status="cancelled", closed_at=dt.datetime.now(dt.UTC))
        self._audit.record(project_id=project_id, agent_role="Agile", event="sprint.cancelled",
                           human_reviewer=user.email, detail={"sprint": it["label"]})
        return self._iteration_view(row)

    # ------------------------------------------------------------------ releases
    async def start_release_hardening(self, project_id: str, user: UserPublic, release_id: str | None = None) -> dict[str, Any]:
        await self.assert_can_run(project_id, user)
        await self._settings(project_id)
        await self.reconcile(project_id)
        releases = await self._db.list_releases(project_id)
        rel = next((r for r in releases if (r["id"] == release_id if release_id else r["status"] == "open")), None)
        if rel is None:
            hardening = next((r for r in releases if r["status"] == "hardening"), None)
            if release_id is None and hardening:
                raise SdlcError("GATE_CONFLICT", f"Release {hardening['code']} is already in hardening")
            raise SdlcError("NOT_FOUND", "Release not found")
        if rel["status"] != "open":
            raise SdlcError("GATE_CONFLICT", f"Release {rel['code']} is already {rel['status']}")
        iterations = await self._db.list_iterations(project_id)
        if any(i["status"] in ("planned", "active") for i in iterations):
            raise SdlcError("GATE_CONFLICT", "Close the current sprint before starting release hardening")
        if not any(i["release_id"] == rel["id"] and i["status"] == "closed" for i in iterations):
            raise SdlcError("GATE_CONFLICT", f"Release {rel['code']} has no completed sprint yet")
        base = await self._base_view(project_id)

        def slots_for(code: str, number: int, existing: list[Any]) -> list[tuple[int, str, str]]:
            refs = [InstanceRef(e["seq"], e["key"], e["base_key"], e["scope"], e["iteration_id"], e["release_id"])
                    for e in existing]
            first = not any(r.scope == "release" for r in refs)
            alloc = allocate_seqs(base["stages"], refs, scope="release", first=first)
            return [(seq, instance_key(k, code), k) for k, seq in alloc.items()]

        try:
            await self._db.create_release_instances(project_id=project_id, release_id=rel["id"], slots_for=slots_for)
        except ValueError as err:
            raise SdlcError("GATE_CONFLICT", str(err)) from err
        wf = await self._workflow.view(project_id)
        stage = next((s for s in wf["stages"] if s.get("releaseId") == rel["id"]), None)
        if stage:
            await self._db.set_project_phase(project_id, stage["seq"], "ACTIVE")
            await self._notify_ready(project_id, [stage])
        self._audit.record(project_id=project_id, agent_role="Agile", event="release.hardening_started",
                           human_reviewer=user.email, detail={"release": rel["code"]})
        return self._release_view(await self._db.get_release(rel["id"]))

    # ------------------------------------------------------------------ lifecycle hooks (called by chat / gates)
    async def after_generation(self, project_id: str, phase: int, actor: str) -> None:
        """A stage just reached PENDING_REVIEW. Best-effort: never blocks the generation result."""
        await self._run_hooks(self._on_generated, project_id, phase, actor, "generated")

    async def on_stage_approved(self, project_id: str, phase: int, actor: str) -> None:
        """A gate was approved. Idempotent: safe to call again (see `reconcile`). Each step has its own failure
        boundary: a failing hook never prevents the lifecycle from advancing, and nothing here can undo the approval."""
        ctx = await self._ctx(project_id, phase, actor)
        if ctx is None:
            return
        for name in ctx.hook_names:
            for fn in self._on_approved.get(name, []):
                try:
                    await fn(ctx)
                except Exception as err:  # noqa: BLE001
                    log.exception("agile approval hook failed project=%s phase=%s", project_id, phase)
                    self._audit.record(project_id=project_id, phase=phase, agent_role="Agile", event="agile.hook_failed",
                                       detail={"stage": ctx.stage["key"], "hook": name, "error": str(err)[:300]})
        try:
            await self._advance_lifecycle(ctx)
        except Exception as err:  # noqa: BLE001
            log.exception("agile lifecycle advance failed project=%s phase=%s", project_id, phase)
            self._audit.record(project_id=project_id, phase=phase, agent_role="Agile", event="agile.hook_failed",
                               detail={"stage": ctx.stage["key"], "hook": "lifecycle", "error": str(err)[:300]})

    async def reconcile(self, project_id: str) -> int:
        """Re-run the (idempotent) approval bookkeeping for every APPROVED stage of the open sprint and of a
        hardening release. Repairs anything a crash or a transient failure left half-done: an unapplied proposal,
        a planned sprint that should be active, a finished sprint that was never closed, a release left open.
        Returns how many stages were revisited."""
        if not await self._db.get_project_agile(project_id):
            return 0
        wf = await self._workflow.view(project_id)
        states = {s["SK"]: s for s in await self._dynamo.list_phase_states(project_id)}
        open_it = await self._db.get_open_iteration(project_id)
        hardening = [r["id"] for r in await self._db.list_releases(project_id) if r["status"] == "hardening"]
        n = 0
        for st in wf["stages"]:
            if (states.get(f"PHASE#{st['seq']}") or {}).get("status") != "APPROVED":
                continue
            mine = (open_it is not None and st.get("iterationId") == open_it["id"]) or st.get("releaseId") in hardening
            if mine:
                await self.on_stage_approved(project_id, st["seq"], "reconcile")
                n += 1
        return n

    async def reconcile_all(self) -> int:
        total = 0
        for pid in await self._db.list_agile_project_ids(any_project=True):
            try:
                total += await self.reconcile(pid)
            except Exception:  # noqa: BLE001
                log.warning("reconcile failed project=%s", pid, exc_info=True)
        return total

    async def assert_stage_mutable(self, project_id: str, stage: dict[str, Any]) -> None:
        """Stages of a closed or cancelled sprint are history: re-running them would rewrite what was delivered."""
        iid = stage.get("iterationId")
        if not iid:
            return
        it = await self._db.get_iteration(iid)
        if it is not None and it["status"] in ("closed", "cancelled"):
            raise SdlcError("GATE_CONFLICT",
                            f"Sprint {it['label']} is {it['status']}; its stages are read-only history")

    async def _run_hooks(self, table: dict[str, list[Hook]], project_id: str, phase: int, actor: str, label: str) -> None:
        ctx = await self._ctx(project_id, phase, actor)
        if ctx is None:
            return
        for name in ctx.hook_names:
            for fn in table.get(name, []):
                try:
                    await fn(ctx)
                except Exception as err:
                    log.exception("agile %s hook failed project=%s phase=%s", label, project_id, phase)
                    self._audit.record(project_id=project_id, phase=phase, agent_role="Agile",
                                       event="agile.hook_failed",
                                       detail={"stage": ctx.stage["key"], "hook": label, "error": str(err)[:300]})

    async def _ctx(self, project_id: str, phase: int, actor: str) -> StageCtx | None:
        if not await self._db.get_project_agile(project_id):
            return None
        wf = await self._workflow.view(project_id)
        stage = next((s for s in wf["stages"] if s["seq"] == phase), None)
        if stage is None:
            return None
        it = await self._db.get_iteration(stage["iterationId"]) if stage.get("iterationId") else None
        rel = await self._db.get_release(stage["releaseId"]) if stage.get("releaseId") else None
        return StageCtx(project_id, stage, it, rel, actor)

    async def _advance_lifecycle(self, ctx: StageCtx) -> None:
        """Structure changes driven by approvals: activate a planned sprint, close a finished one,
        close a hardened release and open the next."""
        role, it, rel = ctx.role, ctx.iteration, ctx.release
        if it and role == "plan" and it["status"] == "planned":
            await self._db.update_iteration(it["id"], status="active", started_at=dt.datetime.now(dt.UTC))
        if it and role == "retro" and it["status"] in ("planned", "active"):
            await self.close_sprint(ctx.project_id, it["id"], ctx.actor)
        if rel and role == "release" and rel["status"] == "hardening":
            nxt = await self._db.close_release_and_open_next(ctx.project_id, rel["id"])   # one transaction
            if nxt is not None:
                self._audit.record(project_id=ctx.project_id, agent_role="Agile", event="release.closed",
                                   detail={"release": rel["code"], "next": nxt["code"]})

    async def close_sprint(self, project_id: str, iteration_id: str, actor: str) -> dict[str, Any]:
        """Idempotent. Done work stays; everything else returns to the backlog; velocity is recorded."""
        it = await self._db.get_iteration(iteration_id)
        if not it or it["status"] not in ("planned", "active"):
            return self._iteration_view(it) if it else {}
        items = await self._db.list_backlog(project_id, iteration_id=iteration_id)
        done = [i for i in items if i["status"] == "done"]
        left = [i for i in items if i["status"] != "done"]
        pts = lambda rows: float(sum(float(r["estimate"] or 0) for r in rows))
        carried = await self._db.release_unfinished_items(iteration_id, to_status="ready")
        summary = {
            "velocity": pts(done), "completed": len(done), "committedPoints": pts(items), "committed": len(items),
            "carried": len(left), "carriedItemIds": carried,
            "doneItemIds": [i["id"] for i in done],
            "completionRate": round(len(done) / len(items), 3) if items else None,
        }
        row = await self._db.update_iteration(
            iteration_id, status="closed", closed_at=dt.datetime.now(dt.UTC), summary=summary)
        self._audit.record(project_id=project_id, agent_role="Agile", event="sprint.closed",
                           human_reviewer=actor, detail={"sprint": it["label"], **{k: v for k, v in summary.items()
                                                                                    if not k.endswith("Ids")}})
        return self._iteration_view(row)

    # ------------------------------------------------------------------ helpers
    async def _base_view(self, project_id: str) -> dict[str, Any]:
        """The derived BASE workflow (one sprint's worth of stages), before expansion."""
        from ..services.workflow import WorkflowConfig, derive

        row = await self._db.get_workflow(project_id)
        if not row:
            raise SdlcError("VALIDATION_FAILED", "No workflow configured")
        return derive(WorkflowConfig.model_validate(row["config"]))

    async def _notify_ready(self, project_id: str, stages: list[dict[str, Any]]) -> None:
        for st in stages:
            try:
                await self._db.insert_notification(
                    project_id=project_id, phase=st["seq"], kind="stage_ready",
                    title=f"{st['name']} is ready to run",
                    body="The sprint's first stage is open.", roles=st.get("team") or [])
            except Exception:
                log.warning("stage_ready notification failed", exc_info=True)


class AgileLifecycle:
    """Glue between generation and approval: called by the chat service when a stage reaches PENDING_REVIEW,
    and when it prepares a sprint/release stage's context."""

    def __init__(self, agile: AgileService, gates: Any, index: Any = None, db: Any = None, jira: Any = None) -> None:
        self._agile, self._gates, self._index, self._db, self._jira = agile, gates, index, db, jira

    async def context_for(self, project_id: str, stage: dict[str, Any], context: list[Any]) -> tuple[list[Any], str]:
        """Bound what a sprint/release stage sees. Raw artifacts of EARLIER sprints are dropped from the window
        (they live in the database and the `.devmind` index); the stage instead gets the budgeted project-memory
        packet plus its own sprint scope. This is what keeps prompts a constant size however long the project runs."""
        if self._jira is not None and stage.get("agileRole") in ("refine", "plan"):
            try:                                   # start refinement/planning from the freshest Jira state
                await self._jira.sync(project_id)
            except Exception:
                log.warning("pre-ceremony Jira sync skipped", exc_info=True)
        wf = await self._agile._workflow.view(project_id)
        keep = {s["seq"] for s in wf["stages"] if s.get("scope", "project") == "project"}
        if stage.get("iterationId"):
            keep |= {s["seq"] for s in wf["stages"] if s.get("iterationId") == stage["iterationId"]}
        if stage.get("releaseId"):
            keep |= {s["seq"] for s in wf["stages"] if s.get("releaseId") == stage["releaseId"]}
        bounded = [a for a in context if a.phase in keep]
        parts: list[str] = []
        scope_items: list[Any] = []
        if stage.get("iterationId") and self._db is not None:
            it = await self._db.get_iteration(stage["iterationId"])
            scope_items = await self._db.list_backlog(project_id, iteration_id=stage["iterationId"])
            if it is not None:
                lines = [f"## Current sprint {it['label']}", f"Goal: {it['goal'] or '(not set)'}",
                         f"Capacity: {float(it['capacity']):g} points"]
                for r in scope_items[:40]:
                    ac = "; ".join((r["acceptance_criteria"] or [])[:3])
                    lines.append(f"- {r['item_key']} [{r['status']}] {r['title']} "
                                 f"({float(r['estimate']) if r['estimate'] is not None else '?'} pts)"
                                 + (f" — AC: {ac}" if ac else ""))
                parts.append("\n".join(lines))
        if self._index is not None:
            comps = tuple(dict.fromkeys(c for r in scope_items for c in (r["components"] or [])))
            packet = await self._index.packet(project_id, story_ids=tuple(r["item_key"] for r in scope_items),
                                              components=comps)
            if packet is not None and packet.text:
                parts.append("## Project memory (DevMind index — authoritative history)\n" + packet.text)
        return bounded, "\n\n".join(parts)

    async def after_review_ready(self, project_id: str, phase: int, *, provider: str, model: str, actor: str) -> None:
        await self._agile.after_generation(project_id, phase, actor)
        # Auto-gate last, so any proposal/index staging above is already in place when it approves.
        await self._gates.try_auto_approve(project_id, phase, provider=provider, model=model)
