"""Persistent content-store tier for stage-generated artifacts.

Two interchangeable backends behind one interface — selected by
CONTENT_STORE_MODE:
  - filesystem: a folder tree on a mounted volume (local Docker), key layout
    content-store/{project}/phase-{n}/{type}-{artefactId}{ext}
  - s3: real S3 (or MinIO) in production, same key layout as the object key.

Artifact bodies live here; Postgres keeps only the storage_key pointer plus a
short summary. Reads fall back to the DB `content` column for rows written
before this tier existed, so the change is backward compatible.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Protocol

from ..config import Settings
from ..domain.errors import SdlcError

_EXT = {
    "OPENAPI": ".yaml", "DBML": ".dbml", "CDK": ".ts", "STRUCTURIZR_DSL": ".dsl",
    "HLD_DIAGRAM": ".mmd", "LLD_DIAGRAM": ".mmd", "PLANTUML": ".puml",
    "ARCH_DIAGRAM": ".svg", "COMPONENT_DIAGRAM": ".svg", "DRAWIO": ".drawio",
    "CUSTOM_DOC": ".md", # fallback type for custom-phase deliverables
    "CLOUDCRAFT_JSON": ".json", "GRAFANA_DASHBOARD": ".json", "POSTMAN_COLLECTION": ".json",
    "XRAY_TESTS": ".json", "K6_SCRIPT": ".js", "GITHUB_ACTIONS": ".yml",
    "DOCKERFILE": "", "APP_CODE": ".txt",
    "HLD": ".md", "LLD": ".md", "PRD": ".md", "ADR": ".md", "TEST_STRATEGY": ".md", "RTM": ".md",
    "EPIC": ".md", "FEATURE": ".md", "USER_STORY": ".md", "PULL_REQUEST": ".md",
    # SDLC toolchain artifacts
    "REST_ASSURED": ".java", "PLAYWRIGHT_SPEC": ".ts", "JMETER_PLAN": ".jmx", "LOCUSTFILE": ".py",
    "SECURITY_SCAN": ".md", "TEST_EXECUTION_REPORT": ".md", "QUALITY_REPORT": ".md",
    "PIPELINE_DESIGN": ".md",
}


def artifact_key(project_id: str, phase: int, artefact_type: str, artefact_id: str) -> str:
    ext = _EXT.get(artefact_type, ".txt")
    return f"content-store/{project_id}/phase-{phase}/{artefact_type}-{artefact_id}{ext}"


def attachment_key(project_id: str, phase: int, attachment_id: str, filename: str) -> str:
    """Key for a user-uploaded stage attachment. Kept in an `attachments/`
    subtree so it never collides with generated artifacts or source files."""
    safe = filename.replace("\\", "/").split("/")[-1] or "file"
    return f"content-store/{project_id}/phase-{phase}/attachments/{attachment_id}-{safe}"


def publish_key(project_id: str, phase: int) -> str:
    """Key for a stage's DEFERRED PUBLISH QUEUE: the external-write tool
    calls (Jira/Confluence/GitHub-docs) captured during generation, replayed only
    after the gate is approved. Kept out of the artifact tree so it is never
    surfaced as a project artifact."""
    return f"content-store/{project_id}/phase-{phase}/_publish-queue.json"


def source_key(project_id: str, phase: int, path: str) -> str:
    """Key for a generated SOURCE file, preserving the repository-relative
    folder structure and the file's real extension (e.g.
    `.../phase-6/src/main/java/com/acme/ItemService.java`). Source files are
    stored as a real tree — not one concatenated blob — so the Files explorer
    shows the project layout and viewers can highlight by extension."""
    safe = path.replace("\\", "/").lstrip("/")
    parts = [p for p in safe.split("/") if p not in ("", ".", "..")]
    if not parts:
        raise ValueError(f"unsafe source path: {path}")
    return f"content-store/{project_id}/phase-{phase}/{'/'.join(parts)}"


class ContentStore(Protocol):
    mode: str

    async def put(self, key: str, body: str) -> None: ...
    async def get(self, key: str) -> str | None: ...
    async def delete_prefix(self, prefix: str) -> int: ...


class FilesystemContentStore:
    """Folder-based store on a mounted volume (local/dev)."""

    mode = "filesystem"

    def __init__(self, root: str) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # keys are '/'-joined and relative; block traversal defensively.
        safe = Path(key.replace("\\", "/"))
        if safe.is_absolute() or ".." in safe.parts:
            raise ValueError(f"unsafe content key: {key}")
        return self._root / safe

    async def put(self, key: str, body: str) -> None:
        def _write() -> None:
            path = self._path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")

        await asyncio.to_thread(_write)

    async def get(self, key: str) -> str | None:
        def _read() -> str | None:
            path = self._path(key)
            return path.read_text(encoding="utf-8") if path.exists() else None

        return await asyncio.to_thread(_read)

    async def delete_prefix(self, prefix: str) -> int:
        """Recursively remove a key subtree (e.g. a whole project's files).
        Returns the number of files removed."""
        def _rmtree() -> int:
            import shutil

            base = self._path(prefix.rstrip("/"))
            if not base.exists() or not base.is_dir():
                return 0
            count = sum(1 for p in base.rglob("*") if p.is_file())
            shutil.rmtree(base)
            return count

        return await asyncio.to_thread(_rmtree)


class S3ContentStore:
    """S3/MinIO object store (production). Reuses the audit S3 client config."""

    mode = "s3"

    def __init__(self, settings: Settings) -> None:
        import boto3
        from botocore.config import Config

        kwargs: dict = {
            "region_name": settings.AWS_REGION,
            "aws_access_key_id": settings.AWS_ACCESS_KEY_ID,
            "aws_secret_access_key": settings.AWS_SECRET_ACCESS_KEY,
            "config": Config(connect_timeout=3, read_timeout=15, retries={"max_attempts": 3}),
        }
        if settings.S3_ENDPOINT:
            kwargs["endpoint_url"] = settings.S3_ENDPOINT
        self._client = boto3.client("s3", **kwargs)
        self._bucket = settings.CONTENT_BUCKET
        self._kms_key = settings.CONTENT_KMS_KEY_ID

    async def ensure_bucket(self, auto_create: bool = False) -> None:
        """Verify the bucket exists. In prod (auto_create=False) it is
        pre-provisioned by IaC — so we only HEAD it and fail fast with a clear
        error if it's missing, and the workload role needs no s3:CreateBucket.
        For local/dev (auto_create=True) create it and enable versioning so
        prior artifact bodies are retained (mirrors prod)."""
        from botocore.exceptions import ClientError

        def _ensure() -> None:
            try:
                self._client.head_bucket(Bucket=self._bucket)
                return
            except ClientError:
                if not auto_create:
                    raise SdlcError(
                        "INTERNAL",
                        f"content bucket '{self._bucket}' not found or not accessible; "
                        "pre-provision it (auto-create is disabled in production)",
                    ) from None
            self._client.create_bucket(Bucket=self._bucket)
            try:
                self._client.put_bucket_versioning(
                    Bucket=self._bucket, VersioningConfiguration={"Status": "Enabled"}
                )
            except ClientError:  # best-effort in dev/MinIO where it may be unsupported
                pass

        await asyncio.to_thread(_ensure)

    def _sse(self) -> dict:
        # When a KMS key is configured, request SSE-KMS explicitly; otherwise send
        # no SSE header so the object inherits the bucket's default encryption.
        return {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": self._kms_key} if self._kms_key else {}

    async def put(self, key: str, body: str) -> None:
        await asyncio.to_thread(
            lambda: self._client.put_object(
                Bucket=self._bucket, Key=key, Body=body.encode(), **self._sse()
            )
        )

    async def get(self, key: str) -> str | None:
        from botocore.exceptions import ClientError

        def _read() -> str | None:
            try:
                obj = self._client.get_object(Bucket=self._bucket, Key=key)
                return obj["Body"].read().decode("utf-8")
            except ClientError:
                return None

        return await asyncio.to_thread(_read)

    async def delete_prefix(self, prefix: str) -> int:
        """Delete every object under a key prefix (a whole project's files).
        Returns the number of objects removed."""
        def _delete() -> int:
            paginator = self._client.get_paginator("list_objects_v2")
            removed = 0
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix.rstrip("/") + "/"):
                objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
                if objs:
                    self._client.delete_objects(Bucket=self._bucket, Delete={"Objects": objs})
                    removed += len(objs)
            return removed

        return await asyncio.to_thread(_delete)


async def build_content_store(settings: Settings) -> ContentStore:
    if settings.CONTENT_STORE_MODE == "s3":
        store = S3ContentStore(settings)
        await store.ensure_bucket(auto_create=settings.CONTENT_BUCKET_AUTO_CREATE)
        return store
    return FilesystemContentStore(settings.CONTENT_STORE_PATH)
