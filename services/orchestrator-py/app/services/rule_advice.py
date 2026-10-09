"""Advice on which rule packs a project should apply, given its profile.

Offered when a project moves to solution architecture (stage 2) and to technical design (stage 3), and only for what the project does not
already have. The profile comes from the organisation default, the project's own choices and what the platform identified in the documents.
"""
from __future__ import annotations

from typing import Any

from ..domain.models import UserPublic
from . import profile as prof
from .rule_packs import recommend

ADVISE_STAGES = (2, 3)


class RuleAdvisor:
    def __init__(self, canon: Any, config: Any) -> None:
        self._canon, self._config = canon, config

    async def recommend(self, project_id: str, user: UserPublic, stage: int | None = None) -> dict[str, Any]:
        await self._canon._authz.assert_project_access(project_id, user)
        catalog = await self._canon.catalog.all()
        p = await self._config.profile(project_id)
        eff = p["effective"]
        rec = recommend(catalog, eff["values"], await self._canon.rule_titles(project_id))
        cfg = await self._config.get(project_id)
        dismissed = bool(stage and cfg["profile"].get("advice", {}).get("dismissed", {}).get(str(stage)))
        primary = rec["bundles"][0] if rec["bundles"] else None
        # What would make a difference now: the best ready-made set, the other matching packs and the essentials, but only what is missing.
        todo = [v for v in ([primary] if primary else []) + rec["packs"] + rec["baseline"] if v and v["missing"] > 0]
        unconfirmed = [i for i in eff["items"] if i["state"] == "identified"]
        return {
            "canAuthor": await self._config.can_edit(project_id, user), "stage": stage, "due": bool(stage in ADVISE_STAGES and todo and not dismissed), "dismissed": dismissed,
            "profile": {"items": eff["items"], "text": prof.describe(eff), "empty": not eff["items"], "unconfirmed": len(unconfirmed)},
            "primary": primary, "bundles": rec["bundles"], "packs": rec["packs"], "baseline": rec["baseline"], "todo": [v["id"] for v in todo],
        }

    async def dismiss(self, project_id: str, user: UserPublic, stage: int) -> None:
        await self._config.dismiss_advice(project_id, user, stage)
