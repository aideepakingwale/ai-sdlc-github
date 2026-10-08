"""A stage cannot be planned, discussed, answered or run before the stages it depends on are approved."""
from types import SimpleNamespace

import pytest

from app.domain.errors import SdlcError
from app.services.chat import ChatService

WF = {"stages": [
    {"seq": 1, "key": "req", "name": "Requirements & Product Definition", "dependsOn": []},
    {"seq": 2, "key": "arch", "name": "Solution Architecture", "dependsOn": ["req"]},
    {"seq": 3, "key": "lld", "name": "Technical Design", "dependsOn": ["req", "arch"]},
]}


def _svc(statuses: dict[int, str]):
    class Dynamo:
        async def list_phase_states(self, pid):
            return [{"SK": f"PHASE#{p}", "status": s} for p, s in statuses.items()]

    svc = ChatService.__new__(ChatService)
    svc._dynamo = Dynamo()

    async def stage_for(pid, phase):
        return WF, next(s for s in WF["stages"] if s["seq"] == phase)

    svc._stage_for = stage_for
    return svc


@pytest.mark.asyncio
async def test_an_entry_stage_has_nothing_to_wait_for():
    assert await _svc({}).unmet_dependencies("p", 1) == []
    await _svc({}).assert_dependencies_approved("p", 1)


@pytest.mark.asyncio
async def test_a_stage_waits_for_every_unapproved_dependency():
    svc = _svc({1: "PENDING_REVIEW", 2: "NOT_STARTED"})
    assert await svc.unmet_dependencies("p", 3) == ["Requirements & Product Definition", "Solution Architecture"]
    with pytest.raises(SdlcError) as e:
        await svc.assert_dependencies_approved("p", 3)
    assert e.value.code == "GATE_CONFLICT" and "waiting for an earlier one" in str(e.value) and "are approved" in str(e.value)


@pytest.mark.asyncio
async def test_one_missing_approval_is_enough_to_block_and_is_named():
    svc = _svc({1: "APPROVED", 2: "AMEND_REQUESTED"})
    with pytest.raises(SdlcError) as e:
        await svc.assert_dependencies_approved("p", 3)
    assert "Solution Architecture is approved" in str(e.value) and "Requirements" not in str(e.value)


@pytest.mark.asyncio
async def test_a_stage_runs_once_everything_it_depends_on_is_approved():
    await _svc({1: "APPROVED", 2: "APPROVED"}).assert_dependencies_approved("p", 3)
