"""The dashboard's portfolio: every visible project with its stage states, at a glance."""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.api.project_routes import portfolio
from app.domain.models import UserPublic


def _proj(i):
    return {"id": f"p{i}", "name": f"P{i}", "status": "ACTIVE", "current_phase": 1, "created_at": datetime.now(UTC),
            "tech_stack": "Java 21 + Spring"}


@pytest.mark.asyncio
async def test_portfolio_summarises_each_project_and_survives_a_broken_one():
    class Wf:
        async def view(self, pid):
            if pid == "p3":
                raise RuntimeError("boom")
            return {"stages": [{"seq": 1, "name": "Req"}, {"seq": 2, "name": "Arch"}, {"seq": 3, "name": "TD"}]}

    class Dyn:
        async def list_phase_states(self, pid):
            return [{"SK": "PHASE#1", "status": "APPROVED", "updatedAt": "2026-10-01T10:00:00"},
                    {"SK": "PHASE#2", "status": "PENDING_REVIEW", "updatedAt": "2026-10-02T10:00:00"}]

    class Db:
        async def list_projects_all(self):
            return [_proj(1), _proj(3)]

    c = SimpleNamespace(db=Db(), workflow=Wf(), dynamo=Dyn())
    out = await portfolio(UserPublic(id="u", email="a@b.c", displayName="A", role="SUPER_ADMIN"), c)
    ok, broken = out["projects"]
    assert ok["approved"] == 1 and ok["awaitingReview"] is True and ok["escalated"] is False
    assert [s["color"] for s in ok["stages"]] == ["emerald", "amber", "slate"]
    assert ok["lastActivity"] == "2026-10-02T10:00:00" and ok["techStack"] == "Java 21 + Spring"
    assert broken["stages"] == [] and broken["name"] == "P3"
