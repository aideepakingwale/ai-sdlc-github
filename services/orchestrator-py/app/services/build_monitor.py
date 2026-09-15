"""Automated Build Recovery Loop (Master Spec §5, Module 6).
State machine in DynamoDB BuildRecoveryTracker; run→tracker index and PR
metadata in Redis. Entry points converge on _advance(): the synchronous drive
after a phase-6 push (mock CI resolves instantly), the GitHub webhook, and the
fallback poller."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any, Callable

from redis.asyncio import Redis

from ..config import Settings
from ..integrations.mcp_client import McpToolClient
from ..repos.aws import DynamoStore
from ..repos.pg import Database, new_id
from .audit import AuditService

log = logging.getLogger("build-monitor")

ACTIVE_SET = "build:active"
META_TTL = 604_800


def _meta_key(tracker_id: str) -> str:
    return f"build:meta:{tracker_id}"


class BuildMonitor:
    def __init__(
        self, dynamo: DynamoStore, redis: Redis, mcp: McpToolClient,
        db: Database, audit: AuditService, settings: Settings,
    ) -> None:
        self._dynamo = dynamo
        self._redis = redis
        self._mcp = mcp
        self._db = db
        self._audit = audit
        self._settings = settings
        self._poll_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------ entry points
    async def start_tracking(
        self, *, run_id: str, project_id: str, branch: str,
        pr_title: str, pr_body: str, checklist: list[str],
        emit: Callable[[dict[str, Any]], None] | None = None,
        stage_seq: int = 6, reviewer_role: str = "DEV",
    ) -> dict[str, Any]:
        meta = {
            "trackerId": run_id, "projectId": project_id, "branch": branch,
            "currentRunId": run_id, "prTitle": pr_title, "prBody": pr_body, "checklist": checklist,
            "stageSeq": stage_seq, "reviewerRole": reviewer_role,
        }
        await self._redis.set(_meta_key(run_id), json.dumps(meta), ex=META_TTL)
        await self._redis.sadd(ACTIVE_SET, run_id)
        await self._redis.set(f"build:run2tracker:{run_id}", run_id, ex=META_TTL)
        await self._dynamo.put_build_tracker(run_id, {
            "projectId": project_id, "branch": branch, "state": "PIPELINE_RUNNING",
            "iterationCount": 0, "failedJobIds": [], "lastRootCause": None,
            "updatedAt": datetime.now(UTC).isoformat(),
        })
        return await self._advance(run_id, emit)

    async def on_webhook_run_completed(self, run_id: str) -> None:
        tracker_id = await self._redis.get(f"build:run2tracker:{run_id}")
        if tracker_id:
            await self._advance(tracker_id.decode() if isinstance(tracker_id, bytes) else tracker_id)

    def start_polling(self) -> None:
        async def _loop() -> None:
            while True:
                await asyncio.sleep(self._settings.BUILD_POLL_INTERVAL_MS / 1000)
                try:
                    for tracker_id in await self._redis.smembers(ACTIVE_SET):
                        tid = tracker_id.decode() if isinstance(tracker_id, bytes) else tracker_id
                        await self._advance(tid)
                except Exception as err:
                    log.error("build poll sweep failed: %s", err)

        self._poll_task = asyncio.get_running_loop().create_task(_loop())

    def stop_polling(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()

    # ------------------------------------------------------------ state machine
    async def _advance(self, tracker_id: str, emit: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        send = emit or (lambda _e: None)
        raw = await self._redis.get(_meta_key(tracker_id))
        if not raw:
            await self._redis.srem(ACTIVE_SET, tracker_id)
            return {"state": "ESCALATED", "iterations": 0}
        meta = json.loads(raw)

        tracker = await self._dynamo.get_build_tracker(tracker_id)
        if not tracker or tracker.get("state") in ("SUCCEEDED", "ESCALATED"):
            await self._redis.srem(ACTIVE_SET, tracker_id)
            done_state = tracker.get("state") if tracker else "ESCALATED"
            return {"state": done_state, "iterations": int(tracker.get("iterationCount", 0)) if tracker else 0}

        while True:
            status = await self._mcp.call("github_poll_run_status", {"runId": meta["currentRunId"]})
            if status["status"] != "completed":
                current = await self._dynamo.get_build_tracker(tracker_id)
                return {"state": "PIPELINE_RUNNING", "iterations": int(current.get("iterationCount", 0)) if current else 0}

            if status["conclusion"] == "success":
                return await self._succeed(tracker_id, meta, send)

            await self._dynamo.set_build_state(tracker_id, "ANALYSING_FAILURE")
            send({"type": "node", "node": "agent",
                  "label": f"CI run {meta['currentRunId']} failed — analysing root cause"})

            current = await self._dynamo.get_build_tracker(tracker_id)
            if current and int(current.get("iterationCount", 0)) >= self._settings.BUILD_LOOP_MAX_ITERATIONS:
                return await self._escalate(tracker_id, meta, int(current["iterationCount"]), send)

            logs = await self._mcp.call("github_fetch_build_logs", {
                "runId": meta["currentRunId"], "failedJobIds": status.get("failedJobIds", []),
            })
            analysis = await self._mcp.call("amazonq_analyse_failure", {
                "rawLogText": logs["rawLogText"], "failedStep": logs["failedStep"],
            })
            iteration = await self._dynamo.increment_build_iteration(
                tracker_id, analysis["rootCause"], status.get("failedJobIds", [])
            )
            self._audit.record(
                project_id=meta["projectId"], phase=int(meta.get("stageSeq", 6)), agent_role="BuildMonitor",
                event="build.failure_analysed",
                detail={"runId": meta["currentRunId"], "iteration": iteration,
                        "rootCauseClass": analysis["rootCauseClass"], "rootCause": analysis["rootCause"]},
            )
            send({"type": "node", "node": "agent",
                  "label": f"Root cause [{analysis['rootCauseClass']}] — generating fix "
                           f"(iteration {iteration}/{self._settings.BUILD_LOOP_MAX_ITERATIONS})"})

            await self._dynamo.set_build_state(tracker_id, "FIXING")
            fix = await self._mcp.call("ghcopilot_generate_fix", {
                "rootCauseClass": analysis["rootCauseClass"], "rootCause": analysis["rootCause"],
                "affectedFiles": analysis.get("affectedFiles", []), "currentFiles": [],
            })
            push = await self._mcp.call("github_commit_fix", {
                "branch": meta["branch"], "files": fix["files"],
                "message": f"{fix['message']} (iter-{iteration})",
            })
            send({"type": "tool_call", "tool": "github_commit_fix", "status": "success",
                  "summary": f"iter-{iteration} -> run {push['runId']}"})

            meta["currentRunId"] = push["runId"]
            await self._redis.set(_meta_key(tracker_id), json.dumps(meta), ex=META_TTL)
            await self._redis.set(f"build:run2tracker:{push['runId']}", tracker_id, ex=META_TTL)
            await self._dynamo.set_build_state(tracker_id, "PIPELINE_RUNNING")

    async def _succeed(self, tracker_id: str, meta: dict, send: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        pr = await self._mcp.call("github_create_pull_request", {
            "branch": meta["branch"], "title": meta["prTitle"],
            "body": meta["prBody"], "checklist": meta["checklist"],
        })
        await self._dynamo.set_build_state(tracker_id, "SUCCEEDED")
        await self._redis.srem(ACTIVE_SET, tracker_id)
        tracker = await self._dynamo.get_build_tracker(tracker_id)
        iterations = int(tracker.get("iterationCount", 0)) if tracker else 0

        seq = int(meta.get("stageSeq", 6))
        reviewer = meta.get("reviewerRole", "DEV")
        assert self._db.pool
        await self._db.pool.execute(
            "INSERT INTO artefacts (id, project_id, phase, type, title, content, url) "
            "VALUES ($1,$2,$3,'PULL_REQUEST',$4,$5,$6)",
            new_id(), meta["projectId"], seq, f"PR #{pr['prNumber']}: {meta['prTitle']}",
            meta["prBody"], pr["url"],
        )
        # Gate goes to human review only after CI is green (Module 6).
        await self._dynamo.put_phase_state(
            project_id=meta["projectId"], phase=seq, status="PENDING_REVIEW", reviewer_role=reviewer,
        )
        self._audit.record(
            project_id=meta["projectId"], phase=seq, agent_role="BuildMonitor", event="build.succeeded",
            detail={"iterations": iterations, "prNumber": pr["prNumber"], "prUrl": pr["url"]},
        )
        send({"type": "artifact", "artifact": {"type": "PULL_REQUEST", "title": f"PR #{pr['prNumber']}", "url": pr["url"]}})
        send({"type": "gate", "phase": seq, "status": "PENDING_REVIEW", "reviewerRole": reviewer})
        return {"state": "SUCCEEDED", "iterations": iterations, "prUrl": pr["url"]}

    async def _escalate(self, tracker_id: str, meta: dict, iterations: int, send: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        seq = int(meta.get("stageSeq", 6))
        reviewer = meta.get("reviewerRole", "DEV")
        await self._dynamo.set_build_state(tracker_id, "ESCALATED")
        await self._redis.srem(ACTIVE_SET, tracker_id)
        await self._dynamo.put_phase_state(
            project_id=meta["projectId"], phase=seq, status="ESCALATED", reviewer_role=reviewer,
            comments=f"Build loop exhausted after {iterations} iterations",
        )
        await self._db.set_project_status(meta["projectId"], "ESCALATED")
        self._audit.record(
            project_id=meta["projectId"], phase=seq, agent_role="BuildMonitor",
            event="build.escalated", detail={"iterations": iterations},
        )
        send({"type": "gate", "phase": seq, "status": "ESCALATED", "reviewerRole": reviewer})
        return {"state": "ESCALATED", "iterations": iterations}
