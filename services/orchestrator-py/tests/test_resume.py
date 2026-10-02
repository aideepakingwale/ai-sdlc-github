"""Resilient generation: per-part persistence, resume after restart, replay buffer."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel

from app.agents.phase_agents import _generate_phase_split, _part_text
from app.domain.models import AgentState
from app.services.generation_jobs import GenerationJobs


class ListRedis:
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
    async def ltrim(self, k, a, b):
        lst = self.lists.get(k, [])
        self.lists[k] = lst[a:] if a < 0 else lst[a:b + 1]
    async def expire(self, k, t): return True
    async def lrange(self, k, a, b):
        lst = self.lists.get(k, [])
        return lst[a:] if b == -1 else lst[a:b + 1]


class JobDb:
    def __init__(self, stale: list[dict]) -> None:
        self.stale, self.created, self.finished = stale, [], []

    async def claim_stale_generation_jobs(self, keep_ids):
        return [r for r in self.stale if r["id"] not in keep_ids]
    async def create_generation_job(self, *a, **k): self.created.append(a)
    async def mark_generation_job_running(self, *_): ...
    async def finish_generation_job(self, *a): self.finished.append(a)


async def test_reconcile_resumes_interrupted_job_and_clears_stale_lock() -> None:
    redis = ListRedis()
    redis.kv["run:p1:2"] = "1"  # stale lock left by the dead process
    db = JobDb([{"id": "old", "project_id": "p1", "phase": 2, "started_by": "sa@x"}])
    jobs = GenerationJobs(db, redis, runner=None)  # type: ignore[arg-type]
    assert await jobs.reconcile() == 1
    assert redis.kv["sdlc:resume:p1:2"] == "1"
    assert len(db.created) == 1                      # re-enqueued (lock was cleared)
    queued = json.loads(redis.lists["genq"][0])
    assert (queued["projectId"], queued["phase"]) == ("p1", 2)


async def test_reconcile_skips_jobs_still_in_queue() -> None:
    redis = ListRedis()
    redis.lists["genq"] = [json.dumps({"jobId": "q1", "projectId": "p", "phase": 1})]
    db = JobDb([{"id": "q1", "project_id": "p", "phase": 1, "started_by": None}])
    assert await GenerationJobs(db, redis, runner=None).reconcile() == 0  # type: ignore[arg-type]


async def test_deltas_are_coalesced_in_replay_buffer() -> None:
    redis = ListRedis()

    async def runner(_p, _ph, _a, emit):
        emit({"type": "content_start", "part": "document", "title": "T"})
        for i in range(500):
            emit({"type": "content_delta", "part": "document", "text": f"{i},"})
        emit({"type": "content_delta", "part": "other", "text": "x"})
        emit({"type": "content_end", "part": "document"})
        emit({"type": "done"})

    jobs = GenerationJobs(JobDb([]), redis, runner=runner)
    await jobs._run_job({"jobId": "j", "projectId": "p", "phase": 1, "actor": "a"})
    events = [json.loads(x) for x in redis.lists["progress:p:1"]]
    deltas = [e for e in events if e["type"] == "content_delta"]
    assert len(events) < 20 and events[-1]["type"] == "done"
    doc = "".join(e["text"] for e in deltas if e["part"] == "document")
    assert doc == "".join(f"{i}," for i in range(500))   # nothing lost, order kept
    assert "run:p:1" not in redis.kv


class Out(BaseModel):
    alpha: str
    beta: str
    gamma: str


class PartDb:
    def __init__(self, parts: list[dict] | None = None) -> None:
        self.parts = {p["field"]: p for p in parts or []}
        self.cleared = False

    async def list_generation_parts(self, *_): return list(self.parts.values())
    async def clear_generation_parts(self, *_):
        self.cleared = True
        self.parts.clear()
    async def upsert_generation_part(self, *, field, status, error, value_json, **_):
        self.parts[field] = {"field": field, "status": status, "error": error, "value_json": value_json}


class FakeLlm:
    def __init__(self, fail: set[str] = frozenset()) -> None:
        self.calls: list[str] = []
        self.fail = fail

    async def generate_json(self, *, tag, schema, **_):
        name = tag.rsplit(":", 1)[1]
        self.calls.append(name)
        if name in self.fail:
            raise RuntimeError("boom")
        res = SimpleNamespace(provider="p", model="m", attempts=[], tier="x", content="",
                              usage={"promptTokens": 1, "completionTokens": 1})
        return schema(**{name: f"{name}-value"}), res


def _state(**kw) -> AgentState:
    return AgentState(project_id="p", session_id="s", user_input="x", current_phase=2, stage_template=2, **kw)


async def _run(db, llm, **kw):
    events: list[dict] = []
    deps = SimpleNamespace(llm=llm, db=db, settings=SimpleNamespace(PER_ARTIFACT_MAX_PARALLEL=2))
    state = _state(**kw)
    await _generate_phase_split(deps=deps, state=state, system="sys", user="user", schema=Out,
                                base_tag="t", intent="standard", max_tokens=100, model=None,
                                emit=events.append)
    return events


async def test_parts_persist_individually_even_if_a_sibling_fails() -> None:
    db = PartDb()
    await _run(db, FakeLlm(fail={"gamma"}))
    assert db.cleared
    statuses = {k: v["status"] for k, v in db.parts.items()}
    assert statuses == {"alpha": "done", "beta": "done", "gamma": "failed"}


async def test_resume_reuses_done_parts_and_generates_only_the_rest() -> None:
    done = {"field": "alpha", "status": "done", "error": None, "value_json": json.dumps("alpha-value")}
    db, llm = PartDb([done]), FakeLlm()
    events = await _run(db, llm, resume=True)
    assert not db.cleared and sorted(llm.calls) == ["beta", "gamma"]
    assert any(e["type"] == "part" and e["part"] == "alpha" and e["text"] == "alpha-value" for e in events)
    assert all(p["status"] == "done" for p in db.parts.values())


def test_part_text_pretty_prints_json() -> None:
    assert _part_text(json.dumps({"a": 1})).startswith("{\n")
    assert _part_text(json.dumps("hi")) == "hi" and _part_text(None) == ""
