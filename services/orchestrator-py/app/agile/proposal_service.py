"""ProposalService — turns agent output into reviewable proposals and applies them on approval."""

from __future__ import annotations

import logging
from typing import Any

from ..domain.errors import SdlcError
from ..domain.models import UserPublic
from .proposals import (
    LlmPlan, LlmRefine, greedy_plan, refine_to_llm, revalidate_plan, sanitise_plan, sanitise_refine,
)

log = logging.getLogger("agile")


def proposal_view(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"], "kind": row["kind"], "phase": row["phase"], "iterationId": row["iteration_id"],
        "status": row["status"], "payload": row["payload"], "warnings": list(row["warnings"] or []),
        "version": row["version"], "createdAt": row["created_at"].isoformat(),
    }


class ProposalService:
    def __init__(self, db: Any, audit: Any, authz: Any, agile: Any) -> None:
        self._db, self._audit, self._authz, self._agile = db, audit, authz, agile

    # ------------------------------------------------------------------ creation (called by the agents)
    async def create_refine(self, project_id: str, phase: int, iteration_id: str | None, llm: LlmRefine,
                            extra_warnings: list[str] | None = None) -> dict[str, Any]:
        existing = await self._db.list_backlog(project_id)
        payload, warnings = sanitise_refine(llm, existing)
        warnings = [*(extra_warnings or []), *warnings]
        row = await self._db.insert_proposal(project_id=project_id, iteration_id=iteration_id, phase=phase,
                                             kind="refine", payload=payload, warnings=warnings)
        return proposal_view(row)

    async def create_plan(self, project_id: str, phase: int, iteration: Any, llm: LlmPlan | None,
                          extra_warnings: list[str] | None = None) -> dict[str, Any]:
        backlog = await self._db.list_backlog(project_id)
        cfg = await self._db.get_project_agile(project_id)
        cap = float(iteration["capacity"])
        wip = cfg["wip_limit"] if cfg else None
        payload, warnings = sanitise_plan(llm or LlmPlan(), backlog, capacity=cap, wip_limit=wip)
        warnings = [*(extra_warnings or []), *warnings]
        if not payload["items"]:
            # The model gave nothing usable → deterministic plan by rank, so a sprint can always be planned.
            fb, more = sanitise_plan(greedy_plan(backlog, capacity=cap), backlog, capacity=cap, wip_limit=wip)
            if fb["items"]:
                warnings += ["The AI plan had no valid items — filled by backlog rank instead"] + more[:3]
                payload = {**fb, "goal": payload["goal"] or fb["goal"], "risks": payload["risks"]}
        row = await self._db.insert_proposal(project_id=project_id, iteration_id=iteration["id"], phase=phase,
                                             kind="plan", payload=payload, warnings=warnings)
        return proposal_view(row)

    # ------------------------------------------------------------------ reads / edits
    async def latest(self, project_id: str, user: UserPublic, phase: int, kind: str) -> dict[str, Any] | None:
        await self._authz.assert_project_access(project_id, user)
        row = await self._db.latest_proposal(project_id, phase, kind)
        return proposal_view(row) if row else None

    async def edit(self, project_id: str, user: UserPublic, proposal_id: str, payload: dict[str, Any],
                   expected_version: int) -> dict[str, Any]:
        await self._agile.assert_can_run(project_id, user)
        row = await self._db.get_proposal(proposal_id)
        if not row or row["project_id"] != project_id:
            raise SdlcError("NOT_FOUND", "Proposal not found")
        if row["status"] != "proposed":
            raise SdlcError("GATE_CONFLICT", f"This proposal is already {row['status']}")
        if row["kind"] == "refine":
            clean, warnings = sanitise_refine(refine_to_llm(payload), await self._db.list_backlog(project_id))
        else:
            it = await self._db.get_iteration(row["iteration_id"])
            if it is None:
                raise SdlcError("NOT_FOUND", "The sprint of this proposal no longer exists")
            clean, warnings = revalidate_plan(payload, await self._db.list_backlog(project_id), capacity=float(it["capacity"]))
        new = await self._db.update_proposal(proposal_id, expected_version=expected_version, payload=clean, warnings=warnings)
        if new is None:
            raise SdlcError("GATE_CONFLICT", "The proposal changed meanwhile — reload and try again")
        self._audit.record(project_id=project_id, phase=row["phase"], agent_role="Backlog",
                           event="proposal.edited", human_reviewer=user.email, detail={"kind": row["kind"]})
        return proposal_view(new)

    # ------------------------------------------------------------------ applying (hooks on stage approval)
    async def apply_refine_hook(self, ctx: Any) -> None:
        row = await self._db.latest_proposal(ctx.project_id, ctx.phase, "refine")
        if not row or row["status"] != "proposed":
            return                                           # nothing to apply, or already applied (idempotent)
        # Re-sanitise against the CURRENT backlog: it may have changed since the proposal was made.
        payload, warnings = sanitise_refine(refine_to_llm(row["payload"]), await self._db.list_backlog(ctx.project_id))
        res = await self._db.apply_refine(project_id=ctx.project_id, proposal_id=row["id"], ops=payload["ops"],
                                          actor_id=None)
        if res is None:
            return
        self._audit.record(project_id=ctx.project_id, phase=ctx.phase, agent_role="Backlog",
                           event="refine.applied", human_reviewer=ctx.actor,
                           detail={k: len(v) if isinstance(v, list) else v for k, v in res.items()} | {
                               "revalidationWarnings": warnings[:10]})

    async def apply_plan_hook(self, ctx: Any) -> None:
        row = await self._db.latest_proposal(ctx.project_id, ctx.phase, "plan")
        if not row or row["status"] != "proposed" or ctx.iteration is None:
            return
        payload, warnings = revalidate_plan(row["payload"], await self._db.list_backlog(ctx.project_id),
                                            capacity=float(ctx.iteration["capacity"]))
        res = await self._db.apply_plan(
            project_id=ctx.project_id, proposal_id=row["id"], iteration_id=ctx.iteration["id"],
            keys=[i["key"] for i in payload["items"]], goal=payload.get("goal", ""), actor_id=None)
        if res is None:
            return
        self._audit.record(project_id=ctx.project_id, phase=ctx.phase, agent_role="Backlog",
                           event="plan.committed", human_reviewer=ctx.actor,
                           detail={"sprint": ctx.iteration["label"], "assigned": res["assigned"],
                                   "skipped": res["skipped"], "points": payload["points"], "warnings": warnings[:10]})
