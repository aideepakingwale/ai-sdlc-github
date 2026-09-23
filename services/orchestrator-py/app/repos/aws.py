"""DynamoDB (gate state + build tracker) and S3 (immutable audit) — boto3 behind asyncio.to_thread."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from ..config import Settings
from ..domain.errors import SdlcError

PHASE_STATE_TABLE = "PhaseState"
BUILD_TRACKER_TABLE = "BuildRecoveryTracker"

BuildState = Literal["PIPELINE_RUNNING", "ANALYSING_FAILURE", "FIXING", "SUCCEEDED", "ESCALATED"]


class DynamoStore:
    def __init__(self, settings: Settings) -> None:
        self._resource = boto3.resource(
            "dynamodb",
            endpoint_url=settings.DYNAMO_ENDPOINT,
            region_name=settings.AWS_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            config=Config(connect_timeout=3, read_timeout=10, retries={"max_attempts": 3}),
        )
        self.phase_table = self._resource.Table(PHASE_STATE_TABLE)
        self.build_table = self._resource.Table(BUILD_TRACKER_TABLE)

    async def ensure_tables(self) -> None:
        def _ensure() -> None:
            client = self._resource.meta.client
            existing = client.list_tables()["TableNames"]
            if PHASE_STATE_TABLE not in existing:
                client.create_table(
                    TableName=PHASE_STATE_TABLE,
                    KeySchema=[
                        {"AttributeName": "PK", "KeyType": "HASH"},
                        {"AttributeName": "SK", "KeyType": "RANGE"},
                    ],
                    AttributeDefinitions=[
                        {"AttributeName": "PK", "AttributeType": "S"},
                        {"AttributeName": "SK", "AttributeType": "S"},
                    ],
                    BillingMode="PAY_PER_REQUEST",
                )
            if BUILD_TRACKER_TABLE not in existing:
                client.create_table(
                    TableName=BUILD_TRACKER_TABLE,
                    KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}],
                    AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"}],
                    BillingMode="PAY_PER_REQUEST",
                )

        await asyncio.to_thread(_ensure)

    async def ping(self) -> None:
        await asyncio.to_thread(
            lambda: self.phase_table.get_item(Key={"PK": "PING", "SK": "PING"})
        )

    # ------------------------------------------------------------ PhaseState
    async def get_phase_state(self, project_id: str, phase: int) -> dict[str, Any] | None:
        res = await asyncio.to_thread(
            lambda: self.phase_table.get_item(Key={"PK": f"PROJECT#{project_id}", "SK": f"PHASE#{phase}"})
        )
        return res.get("Item")

    async def list_phase_states(self, project_id: str) -> list[dict[str, Any]]:
        from boto3.dynamodb.conditions import Key

        res = await asyncio.to_thread(
            lambda: self.phase_table.query(KeyConditionExpression=Key("PK").eq(f"PROJECT#{project_id}"))
        )
        return res.get("Items", [])

    async def delete_phase_states(self, project_id: str) -> int:
        """Remove all PHASE# items for a project ( project deletion)."""
        items = await self.list_phase_states(project_id)

        def _delete() -> int:
            with self.phase_table.batch_writer() as batch:
                for it in items:
                    batch.delete_item(Key={"PK": it["PK"], "SK": it["SK"]})
            return len(items)

        return await asyncio.to_thread(_delete)

    async def put_phase_state(
        self, *, project_id: str, phase: int, status: str, reviewer_role: str,
        reviewed_by: str | None = None, comments: str | None = None,
    ) -> None:
        from datetime import UTC, datetime

        item = {
            "PK": f"PROJECT#{project_id}",
            "SK": f"PHASE#{phase}",
            "status": status,
            "reviewerRole": reviewer_role,
            "reviewedBy": reviewed_by,
            "comments": comments,
            "updatedAt": datetime.now(UTC).isoformat(),
        }
        await asyncio.to_thread(lambda: self.phase_table.put_item(Item=item))

    async def mark_phase_stale(
        self, *, project_id: str, phase: int, reason: str, source_phase: int,
    ) -> None:
        """Flag a stage as stale WITHOUT changing its status (impact propagation):
        an upstream input was re-generated, so this stage's output may be outdated.
        The badge is cleared when the stage is itself re-run (``put_phase_state``
        rewrites the item) or explicitly dismissed (``clear_phase_stale``)."""
        from datetime import UTC, datetime

        def _update() -> None:
            self.phase_table.update_item(
                Key={"PK": f"PROJECT#{project_id}", "SK": f"PHASE#{phase}"},
                UpdateExpression="SET #stale=:t, #sr=:r, #ss=:src, #sts=:ts",
                ExpressionAttributeNames={
                    "#stale": "stale", "#sr": "staleReason", "#ss": "staleSource", "#sts": "staleSince",
                },
                ExpressionAttributeValues={
                    ":t": True, ":r": reason, ":src": source_phase, ":ts": datetime.now(UTC).isoformat(),
                },
            )

        await asyncio.to_thread(_update)

    async def clear_phase_stale(self, *, project_id: str, phase: int) -> None:
        """Remove the stale flag from a stage (it was re-run or accepted as-is)."""
        def _update() -> None:
            self.phase_table.update_item(
                Key={"PK": f"PROJECT#{project_id}", "SK": f"PHASE#{phase}"},
                UpdateExpression="REMOVE #stale, #sr, #ss, #sts",
                ExpressionAttributeNames={
                    "#stale": "stale", "#sr": "staleReason", "#ss": "staleSource", "#sts": "staleSince",
                },
            )

        try:
            await asyncio.to_thread(_update)
        except ClientError:
            pass  # nothing to remove is fine

    async def transition_phase_state(
        self, *, project_id: str, phase: int, expected: str, next_status: str,
        reviewed_by: str | None = None, comments: str | None = None,
    ) -> None:
        """Optimistic-locked gate transition: concurrent reviews cannot double-fire."""
        from datetime import UTC, datetime

        def _update() -> None:
            self.phase_table.update_item(
                Key={"PK": f"PROJECT#{project_id}", "SK": f"PHASE#{phase}"},
                ConditionExpression="#s = :expected",
                UpdateExpression="SET #s=:next, reviewedBy=:rb, comments=:c, updatedAt=:ts",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":expected": expected,
                    ":next": next_status,
                    ":rb": reviewed_by,
                    ":c": comments,
                    ":ts": datetime.now(UTC).isoformat(),
                },
            )

        try:
            await asyncio.to_thread(_update)
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise SdlcError(
                    "GATE_CONFLICT",
                    f"Phase {phase} is not in {expected} state (concurrent review?)",
                ) from err
            raise

    # ------------------------------------------------------------ BuildRecoveryTracker
    async def get_build_tracker(self, run_id: str) -> dict[str, Any] | None:
        res = await asyncio.to_thread(lambda: self.build_table.get_item(Key={"PK": f"BUILD_RUN#{run_id}"}))
        return res.get("Item")

    async def put_build_tracker(self, run_id: str, item: dict[str, Any]) -> None:
        await asyncio.to_thread(
            lambda: self.build_table.put_item(Item={"PK": f"BUILD_RUN#{run_id}", **item})
        )

    async def increment_build_iteration(self, run_id: str, root_cause: str, failed_jobs: list[str]) -> int:
        from datetime import UTC, datetime

        def _update() -> int:
            res = self.build_table.update_item(
                Key={"PK": f"BUILD_RUN#{run_id}"},
                UpdateExpression=(
                    "SET iterationCount = if_not_exists(iterationCount, :zero) + :one, "
                    "lastRootCause=:rc, failedJobIds=:fj, updatedAt=:ts"
                ),
                ExpressionAttributeValues={
                    ":zero": 0, ":one": 1, ":rc": root_cause, ":fj": failed_jobs,
                    ":ts": datetime.now(UTC).isoformat(),
                },
                ReturnValues="UPDATED_NEW",
            )
            return int(res["Attributes"]["iterationCount"])

        return await asyncio.to_thread(_update)

    async def set_build_state(self, run_id: str, state: BuildState) -> None:
        from datetime import UTC, datetime

        await asyncio.to_thread(
            lambda: self.build_table.update_item(
                Key={"PK": f"BUILD_RUN#{run_id}"},
                UpdateExpression="SET #st=:s, updatedAt=:ts",
                ExpressionAttributeNames={"#st": "state"},
                ExpressionAttributeValues={":s": state, ":ts": datetime.now(UTC).isoformat()},
            )
        )


class S3Store:
    def __init__(self, settings: Settings) -> None:
        kwargs: dict[str, Any] = {
            "region_name": settings.AWS_REGION,
            "aws_access_key_id": settings.AWS_ACCESS_KEY_ID,
            "aws_secret_access_key": settings.AWS_SECRET_ACCESS_KEY,
            "config": Config(connect_timeout=3, read_timeout=10, retries={"max_attempts": 3}),
        }
        if settings.S3_ENDPOINT:
            kwargs["endpoint_url"] = settings.S3_ENDPOINT
        self._client = boto3.client("s3", **kwargs)
        self._bucket = settings.AUDIT_BUCKET

    async def ensure_bucket(self) -> None:
        def _ensure() -> None:
            try:
                self._client.head_bucket(Bucket=self._bucket)
            except ClientError:
                self._client.create_bucket(Bucket=self._bucket)

        await asyncio.to_thread(_ensure)

    async def ping(self) -> None:
        await asyncio.to_thread(lambda: self._client.head_bucket(Bucket=self._bucket))

    async def put_audit_object(self, key: str, body: str) -> None:
        await asyncio.to_thread(
            lambda: self._client.put_object(
                Bucket=self._bucket, Key=key, Body=body.encode(), ContentType="application/json"
            )
        )
