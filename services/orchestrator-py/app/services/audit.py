"""Immutable audit trail: S3 JSON object + Postgres index row per event.
Writes are queued off the request path; flush() drains on shutdown."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from ..repos.aws import S3Store
from ..repos.pg import Database

log = logging.getLogger("audit")

_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _ulid(now_ms: int | None = None) -> str:
    ts = now_ms if now_ms is not None else int(time.time() * 1000)
    chars: list[str] = []
    for _ in range(10):
        chars.append(_B32[ts % 32])
        ts //= 32
    head = "".join(reversed(chars))
    tail = "".join(secrets.choice(_B32) for _ in range(16))
    return head + tail


def sha256_hex(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


class AuditService:
    def __init__(self, db: Database, s3: S3Store) -> None:
        self._db = db
        self._s3 = s3
        self._tasks: set[asyncio.Task[None]] = set()

    def record(
        self,
        *,
        project_id: str,
        agent_role: str,
        event: str,
        phase: int | None = None,
        provider: str | None = None,
        model: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        artefact_body: str | None = None,
        human_reviewer: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        task = asyncio.get_running_loop().create_task(
            self._write(
                project_id=project_id, agent_role=agent_role, event=event, phase=phase,
                provider=provider, model=model, prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens, artefact_body=artefact_body,
                human_reviewer=human_reviewer, detail=detail or {},
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def flush(self) -> None:
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _write(self, **event: Any) -> None:
        try:
            event_id = str(uuid.uuid4())
            now = datetime.now(UTC)
            s3_key = f"audit/{event['project_id']}/{now.date().isoformat()}/{_ulid()}.json"
            artefact_hash = sha256_hex(event["artefact_body"]) if event.get("artefact_body") else None

            body = json.dumps({
                "id": event_id,
                "timestamp": now.isoformat(),
                "projectId": event["project_id"],
                "phase": event.get("phase"),
                "agentRole": event["agent_role"],
                "event": event["event"],
                "provider": event.get("provider"),
                "model": event.get("model"),
                "promptTokens": event.get("prompt_tokens"),
                "completionTokens": event.get("completion_tokens"),
                "artefactHash": artefact_hash,
                "humanReviewer": event.get("human_reviewer"),
                "detail": event.get("detail", {}),
            })

            s3_ok = True
            try:
                await self._s3.put_audit_object(s3_key, body)
            except Exception as err:  # audit must never break the pipeline
                s3_ok = False
                log.error("audit S3 write failed: %s", err)

            await self._db.insert_audit_index({
                "id": event_id,
                "project_id": event["project_id"],
                "phase": event.get("phase"),
                "agent_role": event["agent_role"],
                "event": event["event"],
                "provider": event.get("provider"),
                "model": event.get("model"),
                "prompt_tokens": event.get("prompt_tokens"),
                "completion_tokens": event.get("completion_tokens"),
                "artefact_hash": artefact_hash,
                "human_reviewer": event.get("human_reviewer"),
                "s3_key": s3_key,
                "detail": {**event.get("detail", {}), "s3Delivered": s3_ok},
            })
        except Exception as err:
            log.error("audit write failed: %s", err)
