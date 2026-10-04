"""Registers the index/spec lifecycle hooks on the AgileService (kept out of main.py so tests can reuse it)."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("agile")


def register_index_hooks(agile: Any, index: Any, proposals: Any) -> None:
    """When does the project memory change?

    * a stage is GENERATED (reaches PENDING_REVIEW): stage the new memory and QUEUE the commit — it is replayed
      by the publisher when that stage's gate is approved, so nothing reaches the repository before a human says yes;
    * a Build stage is APPROVED: its design delta is merged into the living specs (staged; the sprint's retro
      commit carries it).
    """

    async def project_memory(ctx: Any) -> None:
        await index.stage_project(ctx.project_id)
        await index.queue_publish(ctx.project_id, ctx.phase)

    async def sprint_closed(ctx: Any) -> None:
        if ctx.iteration is None:
            return
        await index.stage_sprint(ctx.project_id, ctx.iteration["id"])
        await index.queue_publish(ctx.project_id, ctx.phase)

    async def release_closed(ctx: Any) -> None:
        if ctx.release is None:
            return
        await index.stage_release(ctx.project_id, ctx.release["id"], closed=True)
        await index.queue_publish(ctx.project_id, ctx.phase, open_pr=True)

    agile.on_generated("key:vision", project_memory)
    agile.on_generated("key:runway", project_memory)
    agile.on_generated("retro", sprint_closed)
    agile.on_generated("release", release_closed)
    agile.on_approved("build", proposals.apply_delta_hook)
