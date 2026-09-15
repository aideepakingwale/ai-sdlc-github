"""Deferred external publication.

Governance rule: a stage's external, side-effecting writes — Jira tickets,
Confluence pages, GitHub doc/design/config commits — must NOT happen during
generation. The phase agents capture them as a queued *publish plan*
(`PhaseAgentResult.publish_actions`); this service persists that plan and, only
after the HITL gate is APPROVED, replays it against the MCP tools — attributed to
the approver — then back-patches the artifacts' pending URLs to the real ones.

Publication is part of the approval transaction: if it fails, the gate stays
PENDING_REVIEW (the caller does not transition) and the queue is preserved so the
approver can retry. On success the queue is cleared.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..domain.errors import SdlcError
from ..repos.pg import Database
from .audit import AuditService
from .content_store import ContentStore, publish_key

log = logging.getLogger("publish")

# Keys that identify a created external entity; used to remap a story's parent
# epic (etc.) from the generation-time stub key to the real key the connector
# assigned, so cross-references stay valid against real Jira/Confluence/GitHub.
_KEY_FIELDS = ("epicKey", "storyKey", "xrayTestKey")


class PublishService:
    def __init__(self, db: Database, content: ContentStore, mcp: Any, audit: AuditService) -> None:
        self._db = db
        self._content = content
        self._mcp = mcp
        self._audit = audit

    async def enqueue(self, project_id: str, phase: int, actions: list[dict[str, Any]]) -> None:
        """Persist (replacing) the phase's deferred publish plan captured during
        generation. An empty list clears any stale plan from a prior run."""
        await self._content.put(publish_key(project_id, phase), json.dumps(actions))

    async def _load(self, project_id: str, phase: int) -> list[dict[str, Any]]:
        raw = await self._content.get(publish_key(project_id, phase))
        if not raw or not raw.strip():
            return []
        try:
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []

    async def has_pending(self, project_id: str, phase: int) -> bool:
        return bool(await self._load(project_id, phase))

    async def publish(self, *, project_id: str, phase: int, approver_email: str) -> dict[str, Any]:
        """Replay the queued external writes for a phase after gate approval.
        Raises SdlcError on the first failure (queue kept for retry); returns a
        summary on success (queue cleared)."""
        actions = await self._load(project_id, phase)
        if not actions:
            return {"published": 0, "results": []}

        key_map: dict[str, str] = {}
        results: list[dict[str, Any]] = []
        for i, action in enumerate(actions):
            tool = action["tool"]
            args = dict(action.get("args") or {})
            stub = action.get("stub") or {}
            # Rewrite cross-references (e.g. a story's epicKey) from stub → real key.
            for kf in _KEY_FIELDS:
                if isinstance(args.get(kf), str) and args[kf] in key_map:
                    args[kf] = key_map[args[kf]]
            try:
                result = await self._mcp.call(tool, args)
            except Exception as err:  # noqa: BLE001 — surfaced to the approver
                self._audit.record(
                    project_id=project_id, phase=phase, agent_role="Publisher",
                    event="publish.failed", human_reviewer=approver_email,
                    detail={"tool": tool, "index": i, "error": str(err)[:300],
                            "remaining": len(actions) - i},
                )
                raise SdlcError(
                    "PROVIDER_ERROR",
                    f"Publishing to the external tool failed at step {i + 1}/{len(actions)} "
                    f"({tool}): {err}. The gate stays pending — fix the integration and approve again.",
                ) from err

            # Record real keys for later cross-reference substitution.
            for kf in _KEY_FIELDS:
                if stub.get(kf) and result.get(kf):
                    key_map[str(stub[kf])] = str(result[kf])
            # Back-patch the artifacts saved with this action's pending sentinel URL.
            sentinel = stub.get("htmlUrl") or stub.get("url")
            real_url = result.get("htmlUrl") or result.get("url")
            if sentinel and str(sentinel).startswith("pending://"):
                try:
                    await self._db.set_artefact_url_where(
                        project_id=project_id, phase=phase, old_url=str(sentinel), new_url=real_url,
                    )
                except Exception as err:  # noqa: BLE001 — cosmetic back-patch
                    log.warning("url back-patch failed for %s: %s", tool, err)
            results.append({"tool": tool, "ref": result.get("epicKey") or result.get("storyKey")
                            or result.get("xrayTestKey") or result.get("pageId")
                            or result.get("commitSha"), "url": real_url})

        self._audit.record(
            project_id=project_id, phase=phase, agent_role="Publisher",
            event="publish.executed", human_reviewer=approver_email,
            detail={"count": len(results), "tools": [r["tool"] for r in results]},
        )
        # Clear the queue only after every action succeeded.
        await self._content.put(publish_key(project_id, phase), "")
        return {"published": len(results), "results": results}
