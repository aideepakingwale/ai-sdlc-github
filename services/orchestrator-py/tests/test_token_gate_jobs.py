"""The token gate at the job queue: a new stage run is refused while tokens are used up; a queued run, once admitted, finishes."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.domain.errors import SdlcError
from app.services import usage_context as uc
from app.services.generation_jobs import GenerationJobs
from app.services.token_gate import TokenGate


class Redis:
    def __init__(self) -> None:
        self.kv: dict[str, Any] = {}
        self.lists: dict[str, list[str]] = {}

    async def get(self, k): return self.kv.get(k)

    async def set(self, k, v, nx=False, ex=None):
        if nx and k in self.kv:
            return None
        self.kv[k] = v
        return True

    async def delete(self, k):
        self.kv.pop(k, None)
        self.lists.pop(k, None)

    async def exists(self, k): return int(k in self.kv)
    async def rpush(self, k, v): self.lists.setdefault(k, []).append(v)
    async def ltrim(self, k, a, b): ...
    async def expire(self, k, t): return True
    async def lrange(self, k, a, b): return self.lists.get(k, [])[a:]


class Db:
    def __init__(self, stale=None) -> None:
        self.created: list[tuple] = []
        self.stale = stale or []

    async def create_generation_job(self, *a, **k): self.created.append(a)
    async def latest_generation_job(self, *a): return None
    async def mark_generation_job_running(self, *_): ...
    async def finish_generation_job(self, *a): ...
    async def get_user_by_email(self, email): return {"id": "u-ann"}
    async def claim_stale_generation_jobs(self, keep_ids): return self.stale


class Repo:
    def __init__(self, balance: int | None) -> None:
        self.balance = balance
        self.blocks: list[dict] = []

    async def gate_state(self, project_id):
        pool = None if self.balance is None else {"id": project_id, "metered": True, "allocated_tokens": 100, "consumed_tokens": 0, "balance_tokens": self.balance}
        return {"org": None, "project": pool}

    async def record_block(self, **kw): self.blocks.append(kw)


@pytest.fixture(autouse=True)
def _ctx():
    uc.reset()
    yield
    uc.reset()


def jobs(balance, runner=None, **gate_kw):
    redis, db = Redis(), Db()
    j = GenerationJobs(db, redis, runner=runner)
    j.gate = TokenGate(Repo(balance), mode=gate_kw.pop("mode", "enforce"), **gate_kw)
    return j, redis, db


async def test_a_new_stage_run_is_refused_without_leaving_a_lock_or_a_job_behind():
    j, redis, db = jobs(-5)
    uc.reset(user_id="u-ann", project_id="p1")
    with pytest.raises(SdlcError) as e:
        await j.enqueue("p1", 2, "ann@t")
    assert e.value.code == "TOKENS_EXHAUSTED" and e.value.details["projectId"] == "p1"
    assert redis.kv == {} and redis.lists == {} and db.created == []                 # nothing was started: no dup-guard lock, no job, no queue entry
    assert j.gate._repo.blocks[0]["entry"] == "enqueue:phase2" and j.gate._repo.blocks[0]["user_id"] == "u-ann"


async def test_a_stage_run_is_queued_when_there_are_tokens_or_the_pool_is_unlimited():
    for balance in (50, None):
        j, redis, db = jobs(balance)
        uc.reset(user_id="u-ann", project_id="p1")
        out = await j.enqueue("p1", 2, "ann@t")
        assert out["status"] == "queued" and len(db.created) == 1 and json.loads(redis.lists["genq"][0])["projectId"] == "p1"


async def test_resuming_an_interrupted_run_after_a_restart_is_not_a_new_task():
    j, redis, db = jobs(-5)
    j._db = Db(stale=[{"id": "old", "project_id": "p1", "phase": 2, "started_by": "ann@t"}])
    assert await j.reconcile() == 1                                                  # it was admitted before the restart: it resumes, even at zero
    assert len(j._db.created) == 1


async def test_a_queued_run_is_admitted_when_a_worker_starts_it_and_finishes_even_if_the_balance_is_gone():
    seen: dict[str, Any] = {}

    async def runner(project_id, phase, actor, emit):
        ctx = uc.current()
        seen.update(admitted=ctx.admitted, task=ctx.task, user=ctx.user_id, project=ctx.project_id, actor_type=ctx.actor_type)
        emit({"type": "done"})

    j, redis, _ = jobs(-999, runner=runner, task_ceiling=1000)                       # the balance ran out while it waited in the queue
    await j._run_job({"jobId": "j1", "projectId": "p1", "phase": 2, "actor": "ann@t"})
    assert seen["admitted"] is True and seen["user"] == "u-ann" and seen["project"] == "p1" and seen["actor_type"] == "job"
    assert seen["task"] is not None and seen["task"].ceiling == 1000                 # and it carries its safety ceiling
    assert j.gate._repo.blocks == []                                                 # a started run is never refused


async def test_an_unlimited_project_gets_no_ceiling_and_a_gate_that_cannot_look_does_not_stop_a_started_run():
    seen: dict[str, Any] = {}

    async def runner(project_id, phase, actor, emit):
        seen["task"] = uc.current().task
        seen["admitted"] = uc.current().admitted
        emit({"type": "done"})

    j, *_ = jobs(None, runner=runner, task_ceiling=1000)
    await j._run_job({"jobId": "j1", "projectId": "p1", "phase": 2, "actor": "ann@t"})
    assert seen == {"task": None, "admitted": True}

    async def broken(_):
        raise RuntimeError("db down")

    j2, *_ = jobs(5, runner=runner, task_ceiling=1000)
    j2.gate._repo.gate_state = broken
    seen.clear()
    await j2._run_job({"jobId": "j2", "projectId": "p1", "phase": 2, "actor": "ann@t"})
    assert seen["admitted"] is True                                                  # it was admitted when queued: no ceiling beats no run
