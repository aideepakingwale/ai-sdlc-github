"""Decoupled background stage generation — queue + worker (D-97 L2 / D-99).

Submission, execution and viewing are fully separated:
  * **Enqueue** (the trigger endpoint) just records a job, pushes it onto a Redis
    queue and returns immediately — no long-held request.
  * **Workers** (a small pool started in the lifespan) consume the queue and run
    the pipeline, publishing ordered progress to a capped Redis list.
  * **Viewing** is on demand: any client streams `progress:{project}:{phase}`
    (replay + live-tail) whenever it wants — including after navigating away.

Durability: a `generation_jobs` row (queued|running|done|failed) is the record;
QUEUED jobs survive an orchestrator restart (they live in the Redis queue and are
picked up on boot), and RUNNING jobs from a dead process are reconciled to failed.
A Redis lock `run:{project}:{phase}` is the atomic dup-guard for the whole
queued+running lifetime. No pipeline.py changes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any, Awaitable, Callable

from redis import exceptions as redis_exceptions

from .telemetry import set_run_context
from ..domain.errors import SdlcError

log = logging.getLogger("generation_jobs")

_QUEUE_KEY = "genq"        # Redis list used as the FIFO job queue
_PROGRESS_CAP = 5000       # keep the last N events per run
_PROGRESS_TTL = 7200       # seconds the buffer lives after last write
_LOCK_TTL = 7200           # dup-guard lock TTL (covers queue wait + run)
# A single generation can now stream for up to ~1h (LLM_STREAM_TIMEOUT_MS, D-102);
# let one SSE viewer connection watch the whole run to completion with margin
# before it self-closes (the browser reconnects anyway, and the job is durable).
_STREAM_MAX_SECONDS = 4200

# runner(project_id, phase, actor_email, emit) -> runs the stage pipeline.
Emit = Callable[[dict[str, Any]], None]
Runner = Callable[[str, int, str, Emit], Awaitable[None]]


class GenerationJobs:
    def __init__(self, db: Any, redis: Any, runner: Runner) -> None:
        self._db = db
        self._redis = redis
        self._runner = runner
        self._workers: list[asyncio.Task[None]] = []

    # ---- keys -----------------------------------------------------------------
    @staticmethod
    def _lock_key(project_id: str, phase: int) -> str:
        return f"run:{project_id}:{phase}"

    @staticmethod
    def _progress_key(project_id: str, phase: int) -> str:
        return f"progress:{project_id}:{phase}"

    @staticmethod
    def _is_terminal(raw: str) -> bool:
        try:
            return json.loads(raw).get("type") in ("done", "error")
        except Exception:
            return False

    # ---- status ---------------------------------------------------------------
    async def is_active(self, project_id: str, phase: int) -> bool:
        """True while a job for this stage is queued or running (lock held)."""
        return bool(await self._redis.exists(self._lock_key(project_id, phase)))

    async def latest(self, project_id: str, phase: int) -> dict[str, Any]:
        row = await self._db.latest_generation_job(project_id, phase)
        active = await self.is_active(project_id, phase)
        return {
            "jobId": row["id"] if row else None,
            "status": row["status"] if row else None,
            # `running` kept for the UI's existing "is there work to watch?" check.
            "running": active,
        }

    async def reconcile(self) -> int:
        """Boot recovery. Jobs a dead process left 'running' (or 'queued' with no queue
        item) are failed, their stale dup-guard lock is cleared, and each is RESUMED:
        re-enqueued with a resume marker so the run reuses every part already persisted
        as done and regenerates only the rest. Jobs still in the queue are untouched."""
        try:
            queued = await self._redis.lrange(_QUEUE_KEY, 0, -1)
            keep: set[str] = set()
            for raw in queued:
                try:
                    keep.add(json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)["jobId"])
                except Exception:  # noqa: BLE001
                    continue
            rows = await self._db.claim_stale_generation_jobs(keep)
        except Exception as err:  # table may not exist yet on a fresh DB pre-migrate
            log.warning("generation-job reconcile skipped: %s", err)
            return 0
        seen: set[tuple[str, int]] = set()
        for row in rows:
            key = (row["project_id"], int(row["phase"]))
            if key in seen:
                continue
            seen.add(key)
            try:
                await self._redis.delete(self._lock_key(*key))
                await self._redis.set(f"sdlc:resume:{key[0]}:{key[1]}", "1", ex=3600)
                await self.enqueue(key[0], key[1], row.get("started_by"))
                log.info("resuming interrupted generation %s phase %s", *key)
            except Exception:  # noqa: BLE001 — never block boot
                log.exception("could not resume generation %s", key)
        return len(rows)

    async def _emit_to_buffer(self, key: str, event: dict[str, Any]) -> None:
        raw = json.dumps(event, separators=(",", ":"))
        await self._redis.rpush(key, raw)
        await self._redis.ltrim(key, -_PROGRESS_CAP, -1)
        await self._redis.expire(key, _PROGRESS_TTL)

    # ---- enqueue --------------------------------------------------------------
    async def enqueue(self, project_id: str, phase: int, started_by: str | None) -> dict[str, Any]:
        """Record a queued job, push it onto the queue and return immediately. If a
        job for this stage is already active, returns that job with alreadyRunning."""
        lock = self._lock_key(project_id, phase)
        acquired = await self._redis.set(lock, "1", nx=True, ex=_LOCK_TTL)
        if not acquired:
            existing = await self._db.latest_generation_job(project_id, phase)
            return {
                "jobId": existing["id"] if existing else None,
                "status": existing["status"] if existing else "running",
                "alreadyRunning": True,
            }
        job_id = uuid.uuid4().hex
        await self._db.create_generation_job(job_id, project_id, phase, started_by, status="queued")
        pkey = self._progress_key(project_id, phase)
        await self._redis.delete(pkey)  # fresh buffer for this run
        await self._emit_to_buffer(pkey, {"type": "node", "node": "queue", "label": "Queued — waiting for a worker…"})
        await self._redis.rpush(
            _QUEUE_KEY, json.dumps({"jobId": job_id, "projectId": project_id, "phase": phase, "actor": started_by}),
        )
        return {"jobId": job_id, "status": "queued", "alreadyRunning": False}

    # ---- workers --------------------------------------------------------------
    def start_workers(self, count: int) -> None:
        for i in range(max(1, count)):
            self._workers.append(asyncio.get_running_loop().create_task(self._worker_loop(i)))
        log.info("started %s generation worker(s)", len(self._workers))

    async def stop_workers(self) -> None:
        for t in self._workers:
            t.cancel()
        for t in self._workers:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._workers.clear()

    async def _worker_loop(self, wid: int) -> None:
        while True:
            try:
                item = await self._redis.blpop(_QUEUE_KEY, timeout=5)
            except asyncio.CancelledError:
                raise
            except redis_exceptions.TimeoutError:
                # Benign: the socket read timed out at the blocking window with no
                # job queued (redis-py raises rather than returning nil when the
                # client has a socket_timeout). Not an error — just loop and wait
                # again. An actually-queued item returns immediately, well within
                # the window, so this never delays real pickups.
                continue
            except Exception as err:  # noqa: BLE001 — real Redis hiccup (down/conn reset)
                log.warning("worker %s blpop error: %s", wid, err)
                await asyncio.sleep(1)
                continue
            if not item:
                continue  # timeout tick
            try:
                payload = json.loads(item[1].decode() if isinstance(item[1], (bytes, bytearray)) else item[1])
            except Exception:
                log.exception("worker %s: bad job payload", wid)
                continue
            await self._run_job(payload)

    async def _run_job(self, payload: dict[str, Any]) -> None:
        job_id = payload["jobId"]
        project_id = payload["projectId"]
        phase = int(payload["phase"])
        actor = payload.get("actor") or ""
        set_run_context(project_id, phase)   # this long-lived worker must not attribute spans to the PREVIOUS job's project
        pkey = self._progress_key(project_id, phase)
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        def emit(event: dict[str, Any]) -> None:
            queue.put_nowait(event)

        async def pump() -> None:  # single writer → preserves event order
            held: dict[str, Any] | None = None
            stop = False
            while not stop:
                event = held if held is not None else await queue.get()
                held = None
                if event is None:
                    break
                # Coalesce consecutive token deltas of one part into one buffered event so
                # a long document never overflows the replay buffer (it would trim the
                # start of the document for a late viewer).
                if event.get("type") == "content_delta":
                    while True:
                        try:
                            nxt = queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        if (nxt is not None and nxt.get("type") == "content_delta"
                                and nxt.get("part") == event.get("part")):
                            event = {**event, "text": event["text"] + nxt["text"]}
                        else:
                            if nxt is None:
                                stop = True
                            else:
                                held = nxt
                            break
                try:
                    await self._emit_to_buffer(pkey, event)
                except Exception:  # progress is best-effort
                    log.exception("progress publish failed")

        await self._db.mark_generation_job_running(job_id)
        pump_task = asyncio.get_running_loop().create_task(pump())
        status, err = "done", None
        try:
            await self._runner(project_id, phase, actor, emit)  # runs the pipeline; emits its own 'done'
        except SdlcError as e:
            emit({"type": "error", "code": e.code, "message": e.message})
            status, err = "failed", e.message
        except Exception as e:  # noqa: BLE001
            log.exception("generation job crashed")
            emit({"type": "error", "code": "INTERNAL", "message": f"Pipeline failed: {e}"})
            status, err = "failed", str(e)
        finally:
            emit(None)
            await pump_task
            try:
                await self._db.finish_generation_job(job_id, status, err)
            except Exception:
                log.exception("finish_generation_job failed")
            await self._redis.delete(self._lock_key(project_id, phase))

    # ---- viewing --------------------------------------------------------------
    async def stream(self, project_id: str, phase: int) -> AsyncIterator[str]:
        """SSE: replay the buffered progress, then live-tail (poll the list) until a
        terminal event or the run ends. Independent of enqueue/execution."""
        key = self._progress_key(project_id, phase)
        idx = 0
        deadline = time.monotonic() + _STREAM_MAX_SECONDS
        yield ":ok\n\n"
        while time.monotonic() < deadline:
            items = await self._redis.lrange(key, idx, -1)
            if items:
                for raw in items:
                    s = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
                    idx += 1
                    yield f"data: {s}\n\n"
                    if self._is_terminal(s):
                        return
                continue
            if not await self.is_active(project_id, phase):
                tail = await self._redis.lrange(key, idx, -1)
                for raw in tail:
                    s = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
                    idx += 1
                    yield f"data: {s}\n\n"
                    if self._is_terminal(s):
                        return
                yield f"data: {json.dumps({'type': 'done', 'note': 'run ended'})}\n\n"
                return
            await asyncio.sleep(0.5)
