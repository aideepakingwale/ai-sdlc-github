"""Registers the index/spec lifecycle hooks on the AgileService (kept out of main.py so tests can reuse it)."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("agile")


def register_index_hooks(agile: Any, index: Any, proposals: Any) -> None:
    """When does the project memory change?

    * the CLOSING stage of a release's sprint stage set (the retro in the standard template) is generated: stage the
      sprint's digest; a release's FIRST sprint stage publishes anything already staged (fork/carry set);
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

    async def sprint_entry(ctx: Any) -> None:
        """The release's first sprint stage was generated: queue whatever the index has staged but not published
        yet (e.g. the carry set of a freshly forked release) so it is committed when that stage is approved."""
        await index.queue_publish(ctx.project_id, ctx.phase)

    agile.on_generated("key:vision", project_memory)
    agile.on_generated("key:runway", project_memory)
    agile.on_generated("sprint-sink", sprint_closed)
    agile.on_generated("sprint-entry", sprint_entry)
    agile.on_generated("release", release_closed)
    agile.on_approved("build", proposals.apply_delta_hook)
