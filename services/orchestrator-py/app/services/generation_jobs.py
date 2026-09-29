"""Durable background stage generation (D-97 Level 2).

A stage run is a long, multi-call operation. It must:
  * survive the client disconnecting (Level 1 already detached it),
  * survive an orchestrator restart being *recorded* (a job row; reconciled on boot),
  * be watchable by any viewer, including one that reconnects after navigating away.

Design (no pipeline.py changes):
  * A Redis lock `run:{project}:{phase}` gives an atomic dup-guard.
  * A `generation_jobs` row is the durable record (running|done|failed).
  * Progress events are appended to a capped Redis list `progress:{project}:{phase}`
    (ordered by a single pump task) so any SSE viewer can REPLAY then live-tail —
    the trigger caller and any reconnecting viewer share the exact same feed.
The pipeline runs as a detached asyncio task; closing a viewer never cancels it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any, Callable

from ..domain.errors import SdlcError

log = logging.getLogger("generation_jobs")

_PROGRESS_CAP = 1000       # keep the last N events per run
_PROGRESS_TTL = 7200       # seconds the buffer lives after last write
_LOCK_TTL = 3600           # safety TTL so a dead run's lock can't wedge a stage
_STREAM_MAX_SECONDS = 3600


class GenerationJobs:
    def __init__(self, db: Any, redis: Any) -> None:
        self._db = db
        self._redis = redis

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

    async def is_running(self, project_id: str, phase: int) -> bool:
        return bool(await self._redis.exists(self._lock_key(project_id, phase)))

    async def latest(self, project_id: str, phase: int) -> dict[str, Any]:
        row = await self._db.latest_generation_job(project_id, phase)
        return {
            "jobId": row["id"] if row else None,
            "status": row["status"] if row else None,
            "running": await self.is_running(project_id, phase),
        }

    async def reconcile(self) -> int:
        """Boot recovery: mark any 'running' job failed (its process is gone). Run
        locks expire on their own TTL. Returns the number reconciled."""
        try:
            return await self._db.fail_stale_generation_jobs()
        except Exception as err:  # table may not exist yet on a fresh DB pre-migrate
            log.warning("generation-job reconcile skipped: %s", err)
            return 0

    async def start(
        self, *, project_id: str, phase: int, started_by: str | None,
        handler: Callable[[Callable[[dict[str, Any]], None]], Any],
    ) -> str | None:
        """Acquire the lock, record the job and launch a detached run. Returns the
        job id, or None if a run for this stage is already in progress."""
        lock = self._lock_key(project_id, phase)
        acquired = await self._redis.set(lock, "1", nx=True, ex=_LOCK_TTL)
        if not acquired:
            return None
        job_id = uuid.uuid4().hex
        await self._db.create_generation_job(job_id, project_id, phase, started_by)
        await self._redis.delete(self._progress_key(project_id, phase))  # fresh buffer
        asyncio.get_running_loop().create_task(self._run(job_id, project_id, phase, handler))
        return job_id

    async def _run(
        self, job_id: str, project_id: str, phase: int,
        handler: Callable[[Callable[[dict[str, Any]], None]], Any],
    ) -> None:
        key = self._progress_key(project_id, phase)
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        def emit(event: dict[str, Any]) -> None:
            queue.put_nowait(event)

        async def pump() -> None:  # single writer → preserves event order
            while True:
                event = await queue.get()
                if event is None:
                    break
                raw = json.dumps(event, separators=(",", ":"))
                try:
                    await self._redis.rpush(key, raw)
                    await self._redis.ltrim(key, -_PROGRESS_CAP, -1)
                    await self._redis.expire(key, _PROGRESS_TTL)
                except Exception:  # progress is best-effort; never fail the run on it
                    log.exception("progress publish failed")

        pump_task = asyncio.get_running_loop().create_task(pump())
        status, err = "done", None
        try:
            await handler(emit)  # runs the pipeline; emits its own terminal 'done'
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

    async def stream(self, project_id: str, phase: int) -> AsyncIterator[str]:
        """SSE: replay the buffered progress, then live-tail (poll the list) until a
        terminal event or the run ends. Independent of the trigger request, so a
        reconnecting viewer resumes seamlessly."""
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
            # No new events buffered — if the run has ended, drain + stop.
            if not await self.is_running(project_id, phase):
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
