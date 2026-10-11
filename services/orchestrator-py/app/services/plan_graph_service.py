"""The "Current plan" graph of a stage (docs/plan-graph.md): gathers the real plan, the stage's agents and the run's events, and hands them to the
pure builder in `plan_graph.py`.

Three views of the same graph: `planned` (what the next run will do), `live` (the run in progress, from its buffered events) and `record` (the last
finished run, stored when it ended so it outlives the event buffer). Building the plan costs no tokens.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ..agents import specialists
from ..domain.errors import SdlcError
from ..domain.models import UserPublic
from . import agent_catalog
from .plan_graph import build_graph, fold_events

log = logging.getLogger("plan-graph")

RECORD_STATUSES = {"PENDING_REVIEW", "APPROVED", "ESCALATED"}      # stages whose last run is still the one that matters


class PlanGraphService:
    def __init__(self, chat: Any, db: Any, redis: Any, llm: Any) -> None:
        self._chat = chat
        self._db = db
        self._redis = redis
        self._llm = llm
        self.jobs: Any = None          # GenerationJobs, set after construction

    # ------------------------------------------------------------------ the plan
    async def _models(self, plan: dict[str, Any]) -> dict[str, str]:
        fallback = ((plan.get("catalog") or {}).get("recommended")) or ""
        out: dict[str, str] = {}
        for role in ("reason", "generate", "light"):
            try:
                chain = await self._llm.route_chain(role)
            except Exception:  # noqa: BLE001 - a display detail
                chain = []
            out[role] = (chain[0] if chain else fallback) or ""
        return out

    async def _planned(self, project_id: str, phase: int, user: UserPublic) -> tuple[dict[str, Any], dict[str, Any]]:
        plan = await self._chat.build_plan(project_id=project_id, phase=phase, user=user, run_intel=False)
        _, stage = await self._chat._stage_for(project_id, phase)
        template = int(stage["template"])
        specs = []
        for s in specialists.BY_TEMPLATE.get(template, []):
            cat = agent_catalog.get(s.id) or {}
            specs.append({"id": s.id, "name": s.name, "role": s.role, "kind": s.kind, "fields": list(s.fields), "artifacts": list(s.artifacts),
                          "needs": list(s.needs), "after": list(s.after), "canon": s.canon, "stack": s.stack, "attachments": s.attachments,
                          "tools": list(cat.get("tools") or [])})
        custom = await self._chat._custom_agents_for(project_id, stage)
        try:
            skills = await self._chat.agent_defs.skills_for_stage(project_id, stage["key"]) if self._chat.agent_defs is not None else []
        except Exception:  # noqa: BLE001
            skills = []
        graph = build_graph(
            stage={"seq": phase, "key": stage["key"], "name": stage["name"], "template": template, "persona": stage.get("persona"),
                   "reviewerRole": stage.get("reviewerRole")},
            plan=plan, specialists=specs, custom=custom, custom_skills=skills, models=await self._models(plan))
        return graph, plan

    async def _events(self, project_id: str, phase: int) -> list[dict[str, Any]]:
        key = self.jobs._progress_key(project_id, phase)
        out = []
        for raw in await self._redis.lrange(key, 0, -1):
            try:
                out.append(json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw))
            except ValueError:
                continue
        return out

    # ------------------------------------------------------------------ the views
    async def view(self, project_id: str, phase: int, user: UserPublic, which: str = "auto") -> dict[str, Any]:
        if which not in ("auto", "planned", "run"):
            raise SdlcError("VALIDATION_FAILED", "view must be auto, planned or run")
        graph, plan = await self._planned(project_id, phase, user)
        job = await self._db.latest_generation_job(project_id, phase)
        active = await self.jobs.is_active(project_id, phase)
        stored = await self._db.get_run_graph(project_id, phase)
        has_record = stored is not None
        status = plan.get("status") or ""
        if which == "planned":
            mode = "planned"
        elif active:
            mode = "live"
        elif which == "run":
            mode = "record" if has_record else "planned"
        else:
            mode = "record" if has_record and status in RECORD_STATUSES else "planned"
        out_graph = graph
        if mode == "live":
            out_graph = fold_events(graph, await self._events(project_id, phase), run={"state": "running", "jobId": job["id"] if job else None})
        elif mode == "record" and stored:
            out_graph = {**stored["graph"], "run": {**stored["graph"].get("run", {}), "jobId": stored["job_id"], "state": stored["status"]}}
        return {"mode": mode, "graph": out_graph, "available": {"planned": True, "run": has_record or active},
                "stageStatus": status, "canEdit": bool(plan.get("canEdit"))}

    # ------------------------------------------------------------------ the record
    async def record(self, project_id: str, phase: int, job_id: str, status: str, user: UserPublic) -> None:
        """Store the folded graph when a run ends. Never raises: the record is a convenience, not part of the run."""
        try:
            graph, _ = await self._planned(project_id, phase, user)
            folded = fold_events(graph, await self._events(project_id, phase), run={"state": status, "jobId": job_id})
            await self._db.save_run_graph(job_id, project_id, phase, status, folded)
        except Exception:  # noqa: BLE001
            log.warning("could not record the run graph for %s/%s", project_id, phase, exc_info=True)
