"""Repository layer over Postgres (asyncpg). Services never write SQL elsewhere."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import asyncpg

from ..domain.models import ContextArtifact


def new_id() -> str:
    return str(uuid.uuid4())


async def _init_conn(conn: asyncpg.Connection) -> None:
    for typ in ("json", "jsonb"):
        await conn.set_type_codec(typ, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


class Database:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(self._dsn, min_size=2, max_size=10, init=_init_conn)

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    async def ping(self) -> None:
        assert self.pool
        await self.pool.fetchval("SELECT 1")

    # ------------------------------------------------------------ migrations
    async def run_migrations(self, migrations_dir: Path) -> list[str]:
        assert self.pool
        applied: list[str] = []
        async with self.pool.acquire() as conn:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            for path in sorted(migrations_dir.glob("*.sql")):
                if await conn.fetchval("SELECT 1 FROM schema_migrations WHERE name=$1", path.name):
                    continue
                async with conn.transaction():
                    await conn.execute(path.read_text(encoding="utf-8"))
                    await conn.execute("INSERT INTO schema_migrations (name) VALUES ($1)", path.name)
                applied.append(path.name)
        return applied

    # ------------------------------------------------------------ users
    async def get_user_by_email(self, email: str) -> asyncpg.Record | None:
        assert self.pool
        return await self.pool.fetchrow("SELECT * FROM users WHERE email=$1", email.lower())

    async def upsert_idp_user(self, *, sub: str, email: str, display_name: str, role: str) -> dict[str, Any]:
        """JIT provisioning (D-14): local row for FK integrity + audit."""
        assert self.pool
        row = await self.pool.fetchrow(
            """
            INSERT INTO users (id, email, display_name, role, password_hash, idp_sub)
            VALUES ($1, $2, $3, $4, 'idp:keycloak', $5)
            ON CONFLICT (email) DO UPDATE
              SET idp_sub = EXCLUDED.idp_sub, role = EXCLUDED.role, display_name = EXCLUDED.display_name
            RETURNING id, email, display_name, role
            """,
            new_id(), email.lower(), display_name, role, sub,
        )
        assert row
        return dict(row)

    async def seed_user(self, *, email: str, display_name: str, role: str, password_hash: str) -> None:
        assert self.pool
        await self.pool.execute(
            """
            INSERT INTO users (id, email, display_name, role, password_hash)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (email) DO UPDATE SET role = EXCLUDED.role
            """,
            new_id(), email.lower(), display_name, role, password_hash,
        )

    async def list_users(self, limit: int = 200) -> list[asyncpg.Record]:
        assert self.pool
        return await self.pool.fetch(
            "SELECT id, email, display_name, role FROM users ORDER BY email LIMIT $1", limit
        )

    # ------------------------------------------------------------ platform settings (D-91)
    async def get_setting(self, key: str) -> str | None:
        assert self.pool
        return await self.pool.fetchval("SELECT value FROM platform_settings WHERE key=$1", key)

    async def set_setting(self, key: str, value: str, updated_by: str | None = None) -> None:
        assert self.pool
        await self.pool.execute(
            "INSERT INTO platform_settings (key, value, updated_by, updated_at) "
            "VALUES ($1, $2, $3, now()) "
            "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, "
            "updated_by=EXCLUDED.updated_by, updated_at=now()",
            key, value, updated_by,
        )

    async def delete_setting(self, key: str) -> None:
        assert self.pool
        await self.pool.execute("DELETE FROM platform_settings WHERE key=$1", key)

    async def list_settings(self, keys: list[str]) -> dict[str, str]:
        assert self.pool
        rows = await self.pool.fetch(
            "SELECT key, value FROM platform_settings WHERE key = ANY($1::text[])", keys
        )
        return {r["key"]: r["value"] for r in rows}

    # ------------------------------------------------------------ generation jobs (D-97 L2)
    async def create_generation_job(
        self, job_id: str, project_id: str, phase: int, started_by: str | None, status: str = "queued",
    ) -> None:
        assert self.pool
        await self.pool.execute(
            "INSERT INTO generation_jobs (id, project_id, phase, status, started_by) "
            "VALUES ($1, $2, $3, $4, $5)",
            job_id, project_id, phase, status, started_by,
        )

    async def mark_generation_job_running(self, job_id: str) -> None:
        assert self.pool
        await self.pool.execute(
            "UPDATE generation_jobs SET status='running', updated_at=now() WHERE id=$1", job_id
        )

    async def finish_generation_job(self, job_id: str, status: str, error: str | None = None) -> None:
        assert self.pool
        await self.pool.execute(
            "UPDATE generation_jobs SET status=$2, error=$3, updated_at=now() WHERE id=$1",
            job_id, status, error,
        )

    async def latest_generation_job(self, project_id: str, phase: int) -> asyncpg.Record | None:
        assert self.pool
        return await self.pool.fetchrow(
            "SELECT * FROM generation_jobs WHERE project_id=$1 AND phase=$2 "
            "ORDER BY created_at DESC LIMIT 1",
            project_id, phase,
        )

    async def claim_stale_generation_jobs(self, keep_ids: set[str] | None = None) -> list[dict]:
        """On boot, fail every job a dead process left behind: 'running' ones, and
        'queued' ones whose queue item is gone (`keep_ids` = ids still in the queue).
        Returns the claimed rows so the caller can resume them."""
        assert self.pool
        rows = await self.pool.fetch(
            "UPDATE generation_jobs SET status='failed', "
            "error=COALESCE(error, 'orchestrator restarted mid-run'), updated_at=now() "
            "WHERE (status='running' OR status='queued') AND NOT (id = ANY($1::text[])) "
            "RETURNING id, project_id, phase, started_by",
            list(keep_ids or ()),
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ projects & sessions
    async def create_project(
        self, *, name: str, created_by: str, tech_stack: str = "",
        integrations: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        assert self.pool
        project_id = new_id()
        ig = integrations or {}
        async with self.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "INSERT INTO projects "
                "(id, name, created_by, tech_stack, github_repo, atlassian_site_url, "
                " jira_project_key, confluence_space_key) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING *",
                project_id, name, created_by, tech_stack,
                ig.get("githubRepo"), ig.get("atlassianSiteUrl"),
                ig.get("jiraProjectKey"), ig.get("confluenceSpaceKey"),
            )
            await conn.execute(
                "INSERT INTO sessions (id, project_id, context_window) VALUES ($1, $2, '[]'::jsonb)",
                new_id(), project_id,
            )
        assert row
        return dict(row)

    async def get_project(self, project_id: str) -> asyncpg.Record | None:
        assert self.pool
        return await self.pool.fetchrow("SELECT * FROM projects WHERE id=$1", project_id)

    async def set_project_stack(self, project_id: str, tech_stack: str, source: str) -> None:
        """Record the project's decided technology stack and who decided it."""
        assert self.pool
        await self.pool.execute(
            "UPDATE projects SET tech_stack=$2, tech_stack_source=$3 WHERE id=$1",
            project_id, tech_stack, source,
        )

    async def update_project_integrations(self, project_id: str, integrations: dict[str, Any]) -> None:
        """Edit a project's GitHub/Atlassian targets after creation (D-62)."""
        assert self.pool
        await self.pool.execute(
            "UPDATE projects SET github_repo=$2, atlassian_site_url=$3, "
            "jira_project_key=$4, confluence_space_key=$5 WHERE id=$1",
            project_id, integrations.get("githubRepo"), integrations.get("atlassianSiteUrl"),
            integrations.get("jiraProjectKey"), integrations.get("confluenceSpaceKey"),
        )

    async def delete_project(self, project_id: str) -> bool:
        """Delete a project and its Postgres rows (D-55). Tables with an ON DELETE
        CASCADE FK go automatically once the project row is removed (migration 0010
        fixed sessions + artefacts). `llm_traces` carries a project_id with no FK,
        so purge it explicitly. `audit_index` is deliberately NOT deleted — it is
        append-only by trigger (D-09) and an audit trail must survive the deletion
        of what it describes (non-repudiation); it has no FK, so retained rows
        don't block the delete. Runs in one transaction. Returns False if absent."""
        assert self.pool
        async with self.pool.acquire() as conn, conn.transaction():
            exists = await conn.fetchval("SELECT 1 FROM projects WHERE id=$1", project_id)
            if not exists:
                return False
            await conn.execute("DELETE FROM llm_traces WHERE project_id=$1", project_id)
            # The project's retrieval corpus (approved artefacts, uploaded code): scope = project id, no FK.
            await conn.execute("DELETE FROM kb_documents WHERE scope=$1", project_id)
            await conn.execute("DELETE FROM projects WHERE id=$1", project_id)
        return True

    async def list_projects_all(self) -> list[asyncpg.Record]:
        assert self.pool
        return await self.pool.fetch("SELECT * FROM projects ORDER BY created_at DESC LIMIT 100")

    async def list_projects_by_creator(self, user_id: str) -> list[asyncpg.Record]:
        assert self.pool
        return await self.pool.fetch(
            "SELECT * FROM projects WHERE created_by=$1 ORDER BY created_at DESC LIMIT 100", user_id
        )

    async def list_projects_by_member(self, user_id: str) -> list[asyncpg.Record]:
        assert self.pool
        return await self.pool.fetch(
            """
            SELECT p.* FROM projects p
            JOIN project_members m ON m.project_id = p.id
            WHERE m.user_id = $1 ORDER BY p.created_at DESC LIMIT 100
            """,
            user_id,
        )

    async def set_project_phase(self, project_id: str, phase: int, status: str) -> None:
        assert self.pool
        await self.pool.execute(
            "UPDATE projects SET current_phase=$2, status=$3, updated_at=now() WHERE id=$1",
            project_id, phase, status,
        )
        await self.pool.execute(
            "UPDATE sessions SET current_phase=$2, updated_at=now() WHERE project_id=$1", project_id, phase
        )

    async def set_project_status(self, project_id: str, status: str) -> None:
        assert self.pool
        await self.pool.execute("UPDATE projects SET status=$2, updated_at=now() WHERE id=$1", project_id, status)

    async def get_session(self, project_id: str) -> asyncpg.Record | None:
        assert self.pool
        return await self.pool.fetchrow("SELECT * FROM sessions WHERE project_id=$1", project_id)

    async def update_context_window(self, session_id: str, artifacts: list[ContextArtifact]) -> None:
        assert self.pool
        # The pool's jsonb codec serialises Python objects — do NOT pre-dump
        # (double encoding round-trips as a string, not a list).
        await self.pool.execute(
            "UPDATE sessions SET context_window=$2::jsonb, updated_at=now() WHERE id=$1",
            session_id, [a.model_dump(exclude_none=True) for a in artifacts],
        )

    # ------------------------------------------------------------ members (D-15)
    async def get_membership_role(self, project_id: str, user_id: str) -> str | None:
        assert self.pool
        return await self.pool.fetchval(
            "SELECT role FROM project_members WHERE project_id=$1 AND user_id=$2", project_id, user_id
        )

    async def list_members(self, project_id: str) -> list[asyncpg.Record]:
        assert self.pool
        return await self.pool.fetch(
            """
            SELECT m.user_id, m.role, m.added_at, u.email, u.display_name
            FROM project_members m JOIN users u ON u.id = m.user_id
            WHERE m.project_id=$1 ORDER BY m.added_at
            """,
            project_id,
        )

    async def add_member(self, *, project_id: str, user_id: str, role: str, added_by: str) -> None:
        assert self.pool
        await self.pool.execute(
            """
            INSERT INTO project_members (project_id, user_id, role, added_by)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (project_id, user_id) DO UPDATE SET role=EXCLUDED.role, added_by=EXCLUDED.added_by
            """,
            project_id, user_id, role, added_by,
        )

    async def remove_member(self, project_id: str, user_id: str) -> None:
        assert self.pool
        await self.pool.execute(
            "DELETE FROM project_members WHERE project_id=$1 AND user_id=$2", project_id, user_id
        )

    # ------------------------------------------------------------ artefacts
    async def insert_artefact(
        self, *, project_id: str, phase: int, type_: str, title: str, content: str, url: str | None,
        storage_key: str | None = None, storage_mode: str | None = None, artefact_id: str | None = None,
        lineage_id: str | None = None, version: int = 1,
    ) -> str:
        """When storage_key is set, the body lives in the content-store tier
        (D-23) and only a short pointer/preview is kept in the `content` column.
        Each row is a version in its lineage; the newest is is_latest=true."""
        assert self.pool
        artefact_id = artefact_id or new_id()
        lineage_id = lineage_id or artefact_id
        db_content = content if storage_key is None else content[:2000]
        await self.pool.execute(
            "INSERT INTO artefacts (id, project_id, phase, type, title, content, url, storage_key, storage_mode, "
            " lineage_id, version, is_latest) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, true)",
            artefact_id, project_id, phase, type_, title, db_content, url, storage_key, storage_mode,
            lineage_id, version,
        )
        return artefact_id

    async def latest_artefact_version(
        self, project_id: str, phase: int, type_: str, title: str,
    ) -> asyncpg.Record | None:
        """Most recent version of a logical artifact (matched by phase/type/title)
        across all versions — used to compute the next version + lineage when a
        stage regenerates."""
        assert self.pool
        return await self.pool.fetchrow(
            "SELECT lineage_id, version FROM artefacts "
            "WHERE project_id=$1 AND phase=$2 AND type=$3 AND title=$4 ORDER BY version DESC LIMIT 1",
            project_id, phase, type_, title,
        )

    async def list_artefact_versions(self, project_id: str, lineage_id: str) -> list[asyncpg.Record]:
        """All versions of one logical artifact, newest first (version history)."""
        assert self.pool
        return await self.pool.fetch(
            "SELECT id, version, is_latest, created_at, title, type FROM artefacts "
            "WHERE project_id=$1 AND lineage_id=$2 ORDER BY version DESC",
            project_id, lineage_id,
        )

    async def supersede_phase_artefacts(self, project_id: str, phase: int) -> int:
        """Mark a stage's current artifacts as superseded (retained as history) on
        amend/retrigger — the versioning replacement for delete_phase_artefacts.
        Bodies stay in the content-store so old versions remain viewable."""
        assert self.pool
        result = await self.pool.execute(
            "UPDATE artefacts SET is_latest=false, superseded_at=now() "
            "WHERE project_id=$1 AND phase=$2 AND is_latest=true",
            project_id, phase,
        )
        return int(result.rsplit(" ", 1)[-1]) if result else 0

    async def latest_artefact_row(self, project_id: str, phase: int, type_: str, title: str) -> asyncpg.Record | None:
        assert self.pool
        return await self.pool.fetchrow(
            "SELECT * FROM artefacts WHERE project_id=$1 AND phase=$2 AND type=$3 AND title=$4 ORDER BY version DESC LIMIT 1",
            project_id, phase, type_, title)

    async def list_artefacts(self, project_id: str) -> list[asyncpg.Record]:
        assert self.pool
        return await self.pool.fetch(
            "SELECT * FROM artefacts WHERE project_id=$1 AND is_latest ORDER BY created_at DESC LIMIT 500", project_id
        )

    async def list_phase_artefacts(self, project_id: str, phase: int) -> list[asyncpg.Record]:
        assert self.pool
        return await self.pool.fetch(
            "SELECT * FROM artefacts WHERE project_id=$1 AND phase=$2 ORDER BY created_at DESC LIMIT 500",
            project_id, phase,
        )

    async def list_phase_audit(self, project_id: str, phase: int) -> list[asyncpg.Record]:
        """A phase's activity log = the 'tasks' performed in that stage."""
        assert self.pool
        return await self.pool.fetch(
            "SELECT * FROM audit_index WHERE project_id=$1 AND phase=$2 ORDER BY timestamp DESC LIMIT 200",
            project_id, phase,
        )

    async def get_artefact(self, artefact_id: str) -> asyncpg.Record | None:
        assert self.pool
        return await self.pool.fetchrow("SELECT * FROM artefacts WHERE id=$1", artefact_id)

    async def update_artefact_content(self, artefact_id: str, content: str) -> int:
        """Replace an artifact's body in place and bump its version (D-58 diagram
        repair). Returns the new version. `content` is the DB column value — the
        caller writes the full body to the content-store tier separately."""
        assert self.pool
        return await self.pool.fetchval(
            "UPDATE artefacts SET content=$2, version=version+1 WHERE id=$1 RETURNING version",
            artefact_id, content,
        )

    async def set_artefact_url_where(
        self, *, project_id: str, phase: int, old_url: str, new_url: str | None,
    ) -> int:
        """Back-patch artifact URLs after deferred publish (D-67): every artifact
        saved during generation with the pending sentinel `old_url` gets the real
        external URL once the gate is approved and publication runs. Returns the
        number of rows updated."""
        assert self.pool
        result = await self.pool.execute(
            "UPDATE artefacts SET url=$4 WHERE project_id=$1 AND phase=$2 AND url=$3",
            project_id, phase, old_url, new_url,
        )
        # asyncpg returns e.g. "UPDATE 3"
        return int(result.rsplit(" ", 1)[-1]) if result else 0

    async def delete_phase_artefacts(self, project_id: str, phase: int) -> list[asyncpg.Record]:
        """Remove a stage's artifacts (on retrigger, D-24); returns deleted rows
        (with storage_key) so the caller can purge the content-store too."""
        assert self.pool
        return await self.pool.fetch(
            "DELETE FROM artefacts WHERE project_id=$1 AND phase=$2 RETURNING id, storage_key",
            project_id, phase,
        )

    # ------------------------------------------------------------ notifications (D-53)
    async def insert_notification(
        self, *, project_id: str, phase: int | None, kind: str, title: str,
        body: str = "", roles: list[str] | None = None,
    ) -> str:
        """Durable stage-ready/project-completed notification. `roles` targets the
        stage's team ([] = every project member); read state is per-user in read_by."""
        assert self.pool
        notification_id = new_id()
        await self.pool.execute(
            "INSERT INTO notifications (id, project_id, phase, kind, title, body, roles) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb)",
            notification_id, project_id, phase, kind, title, body, roles or [],
        )
        return notification_id

    async def list_notifications(self, project_id: str, limit: int = 50) -> list[asyncpg.Record]:
        assert self.pool
        return await self.pool.fetch(
            "SELECT * FROM notifications WHERE project_id=$1 ORDER BY created_at DESC LIMIT $2",
            project_id, limit,
        )

    async def mark_notification_read(self, notification_id: str, user_id: str, project_id: str) -> None:
        """Append the user to read_by (idempotent — `?` is jsonb array containment). Only a notification of the
        project the caller is acting in."""
        assert self.pool
        await self.pool.execute(
            "UPDATE notifications SET read_by = read_by || to_jsonb($2::text) "
            "WHERE id=$1 AND project_id=$3 AND NOT (read_by ? $2)",
            notification_id, user_id, project_id,
        )

    # ------------------------------------------------------------ stage attachments (D-54)
    async def insert_attachment(
        self, *, project_id: str, phase: int, filename: str, content_type: str,
        size_bytes: int, is_text: bool, storage_key: str, created_by: str | None,
        attachment_id: str | None = None, extraction: dict[str, Any] | None = None,
    ) -> str:
        assert self.pool
        # Caller may supply the id so the DB row, the content-store key and the
        # id returned to the client all agree (D-54).
        attachment_id = attachment_id or new_id()
        await self.pool.execute(
            "INSERT INTO stage_attachments "
            "(id, project_id, phase, filename, content_type, size_bytes, is_text, storage_key, created_by, extraction) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)",
            attachment_id, project_id, phase, filename, content_type, size_bytes,
            is_text, storage_key, created_by, json.dumps(extraction or {}),
        )
        return attachment_id

    async def list_attachments(self, project_id: str, phase: int) -> list[asyncpg.Record]:
        assert self.pool
        return await self.pool.fetch(
            "SELECT * FROM stage_attachments WHERE project_id=$1 AND phase=$2 ORDER BY created_at",
            project_id, phase,
        )

    async def get_attachment(self, attachment_id: str) -> asyncpg.Record | None:
        assert self.pool
        return await self.pool.fetchrow("SELECT * FROM stage_attachments WHERE id=$1", attachment_id)

    async def get_attachments_by_ids(self, ids: list[str]) -> list[asyncpg.Record]:
        assert self.pool
        if not ids:
            return []
        return await self.pool.fetch("SELECT * FROM stage_attachments WHERE id = ANY($1::text[])", ids)

    async def delete_attachment(self, attachment_id: str, project_id: str) -> asyncpg.Record | None:
        """Delete an attachment of THIS project (an id from another project matches nothing)."""
        assert self.pool
        return await self.pool.fetchrow(
            "DELETE FROM stage_attachments WHERE id=$1 AND project_id=$2 RETURNING id, storage_key",
            attachment_id, project_id,
        )

    async def get_artefacts_by_ids(self, ids: list[str]) -> list[asyncpg.Record]:
        """Resolve a curated set of artifact @references for injection (D-54)."""
        assert self.pool
        if not ids:
            return []
        return await self.pool.fetch("SELECT * FROM artefacts WHERE id = ANY($1::text[])", ids)

    # ------------------------------------------------------------ stage plan drafts (D-56)
    async def get_stage_plan(self, project_id: str, phase: int) -> asyncpg.Record | None:
        assert self.pool
        return await self.pool.fetchrow(
            "SELECT * FROM stage_plans WHERE project_id=$1 AND phase=$2", project_id, phase
        )

    async def set_stage_plan_intel(self, project_id: str, phase: int, obj: dict | None) -> None:
        """Persist {sig, plan} - the AI's analysis of the stage plan - beside the plan row."""
        assert self.pool
        await self.pool.execute(
            """INSERT INTO stage_plans (project_id, phase, plan_intel) VALUES ($1,$2,$3::jsonb)
               ON CONFLICT (project_id, phase) DO UPDATE SET plan_intel=EXCLUDED.plan_intel""",
            project_id, phase, None if obj is None else json.dumps(obj))

    async def insert_context_manifest(self, project_id: str, phase: int, manifest: dict, created_by: str | None) -> str:
        """Store what a stage knew for one run; keep only the latest few per stage."""
        assert self.pool
        mid = new_id()
        await self.pool.execute(
            "INSERT INTO context_manifests (id, project_id, phase, manifest, created_by) VALUES ($1,$2,$3,$4::jsonb,$5)",
            mid, project_id, phase, json.dumps(manifest), created_by)
        await self.pool.execute(
            """DELETE FROM context_manifests WHERE project_id=$1 AND phase=$2 AND id NOT IN
               (SELECT id FROM context_manifests WHERE project_id=$1 AND phase=$2 ORDER BY created_at DESC LIMIT 10)""",
            project_id, phase)
        return mid

    async def list_context_manifests(self, project_id: str, phase: int, limit: int = 2) -> list[dict]:
        assert self.pool
        rows = await self.pool.fetch(
            "SELECT id, manifest, created_at FROM context_manifests WHERE project_id=$1 AND phase=$2 ORDER BY created_at DESC LIMIT $3",
            project_id, phase, limit)
        return [{"id": r["id"], "createdAt": r["created_at"].isoformat(),
                 "manifest": json.loads(r["manifest"]) if isinstance(r["manifest"], str) else r["manifest"]} for r in rows]

    async def latest_context_manifests(self, project_id: str) -> list[dict]:
        """The most recent manifest of every stage that has run (pipeline overview)."""
        assert self.pool
        rows = await self.pool.fetch(
            """SELECT DISTINCT ON (phase) id, phase, manifest, created_at FROM context_manifests
               WHERE project_id=$1 ORDER BY phase, created_at DESC""", project_id)
        return [{"id": r["id"], "phase": r["phase"], "createdAt": r["created_at"].isoformat(),
                 "manifest": json.loads(r["manifest"]) if isinstance(r["manifest"], str) else r["manifest"]} for r in rows]

    # ------------------------------------------------------------------ code plans (two-step code generation)
    @staticmethod
    def _code_plan(r: asyncpg.Record | None) -> dict | None:
        if not r:
            return None
        d = dict(r)
        for k in ("structure", "meta"):
            if isinstance(d.get(k), str):
                d[k] = json.loads(d[k])
        return d

    async def latest_code_plan(self, project_id: str, phase: int) -> dict | None:
        """The newest proposal that is not superseded (proposed or approved), else None."""
        assert self.pool
        return self._code_plan(await self.pool.fetchrow(
            """SELECT * FROM code_plans WHERE project_id=$1 AND phase=$2 AND status <> 'superseded'
               ORDER BY version DESC LIMIT 1""", project_id, phase))

    async def insert_code_plan(self, *, project_id: str, phase: int, structure: dict, meta: dict,
                               artefact_id: str | None, proposed_by: str | None) -> dict:
        """Add a new proposal; any earlier un-approved proposal is superseded (an approved one is kept
        approved only until the new one is approved - see approve_code_plan)."""
        assert self.pool
        async with self.pool.acquire() as conn, conn.transaction():
            ver = await conn.fetchval("SELECT COALESCE(MAX(version),0)+1 FROM code_plans WHERE project_id=$1 AND phase=$2", project_id, phase)
            await conn.execute("UPDATE code_plans SET status='superseded' WHERE project_id=$1 AND phase=$2 AND status <> 'superseded'", project_id, phase)
            row = await conn.fetchrow(
                """INSERT INTO code_plans (id, project_id, phase, version, status, structure, meta, artefact_id, proposed_by)
                   VALUES ($1,$2,$3,$4,'proposed',$5::jsonb,$6::jsonb,$7,$8) RETURNING *""",
                new_id(), project_id, phase, ver, json.dumps(structure), json.dumps(meta), artefact_id, proposed_by)
        return self._code_plan(row)  # type: ignore[return-value]

    async def approve_code_plan(self, plan_id: str, *, approver: str, comments: str | None = None) -> dict | None:
        assert self.pool
        return self._code_plan(await self.pool.fetchrow(
            """UPDATE code_plans SET status='approved', decided_by=$2, decided_at=now(), comments=$3
               WHERE id=$1 AND status='proposed' RETURNING *""", plan_id, approver, comments))

    async def supersede_code_plan(self, plan_id: str) -> None:
        assert self.pool
        await self.pool.execute("UPDATE code_plans SET status='superseded' WHERE id=$1", plan_id)

    async def mark_code_plan_implemented(self, plan_id: str) -> None:
        assert self.pool
        await self.pool.execute("UPDATE code_plans SET implemented_at=now() WHERE id=$1", plan_id)

    async def mark_code_plan_committed(self, plan_id: str, commit_ref: str) -> None:
        assert self.pool
        await self.pool.execute("UPDATE code_plans SET committed_at=now(), commit_ref=$2 WHERE id=$1", plan_id, commit_ref)

    async def set_code_plan_artefact(self, plan_id: str, artefact_id: str) -> None:
        assert self.pool
        await self.pool.execute("UPDATE code_plans SET artefact_id=$2 WHERE id=$1", plan_id, artefact_id)

    async def set_stage_plan_sig(self, project_id: str, phase: int, sig: str | None) -> None:
        """Remember the input signature the plan was last built for; a later difference means
        the plan is stale and must be reviewed again before generating."""
        assert self.pool
        await self.pool.execute(
            """INSERT INTO stage_plans (project_id, phase, plan_sig) VALUES ($1,$2,$3)
               ON CONFLICT (project_id, phase) DO UPDATE SET plan_sig=EXCLUDED.plan_sig""",
            project_id, phase, sig)

    async def upsert_stage_plan(
        self, *, project_id: str, phase: int, prompt_overlay: str,
        referenced_artifact_ids: list[str], attachment_ids: list[str],
        formwork_ids: list[str], origin: str, updated_by: str | None,
        step_overrides: dict | None = None, artifact_formats: dict | None = None,
    ) -> None:
        """Insert/update a stage's plan. `artifact_formats=None` KEEPS the stored per-artifact
        formats (several callers rewrite a plan without knowing them); pass {} to clear."""
        assert self.pool
        import json as _json
        await self.pool.execute(
            "INSERT INTO stage_plans "
            "(project_id, phase, prompt_overlay, referenced_artifact_ids, attachment_ids, formwork_ids, "
            " step_overrides, origin, updated_by, artifact_formats, updated_at) "
            "VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,$6::jsonb,$7::jsonb,$8,$9, COALESCE($10::jsonb, '{}'::jsonb), now()) "
            "ON CONFLICT (project_id, phase) DO UPDATE SET "
            "prompt_overlay=EXCLUDED.prompt_overlay, referenced_artifact_ids=EXCLUDED.referenced_artifact_ids, "
            "attachment_ids=EXCLUDED.attachment_ids, formwork_ids=EXCLUDED.formwork_ids, "
            "step_overrides=EXCLUDED.step_overrides, "
            "artifact_formats=COALESCE($10::jsonb, stage_plans.artifact_formats), "
            "origin=EXCLUDED.origin, updated_by=EXCLUDED.updated_by, updated_at=now()",
            project_id, phase, prompt_overlay, referenced_artifact_ids, attachment_ids,
            formwork_ids, _json.dumps(step_overrides or {}), origin, updated_by,
            None if artifact_formats is None else _json.dumps(artifact_formats),
        )

    async def set_stage_amend(self, project_id: str, phase: int, mode: str | None, base: str | None = None,
                              *, keep_base: bool = False) -> None:
        """Record how an amendment is being re-planned. `keep_base` leaves the stored base untouched."""
        assert self.pool
        if keep_base:
            await self.pool.execute("UPDATE stage_plans SET amend_mode=$3 WHERE project_id=$1 AND phase=$2", project_id, phase, mode)
        else:
            await self.pool.execute("UPDATE stage_plans SET amend_mode=$3, amend_base=$4 WHERE project_id=$1 AND phase=$2",
                                    project_id, phase, mode, base)

    async def delete_stage_traits(self, project_id: str, phase: int) -> None:
        assert self.pool
        await self.pool.execute("DELETE FROM stage_traits WHERE project_id=$1 AND phase=$2", project_id, phase)

    async def archive_stage_plan(self, project_id: str, phase: int) -> None:
        """Keep a copy of the stage's plan before it is consumed by a run, so an amendment can extend it."""
        assert self.pool
        await self.pool.execute(
            "INSERT INTO stage_plan_history (id, project_id, phase, prompt_overlay, referenced_artifact_ids, attachment_ids, "
            "formwork_ids, step_overrides, artifact_formats, origin) "
            "SELECT $3, project_id, phase, COALESCE(prompt_overlay,''), COALESCE(referenced_artifact_ids,'[]'::jsonb), "
            "COALESCE(attachment_ids,'[]'::jsonb), COALESCE(formwork_ids,'[]'::jsonb), COALESCE(step_overrides,'{}'::jsonb), "
            "COALESCE(artifact_formats,'{}'::jsonb), COALESCE(origin,'new') FROM stage_plans WHERE project_id=$1 AND phase=$2",
            project_id, phase, new_id())
        await self.pool.execute(
            "DELETE FROM stage_plan_history WHERE project_id=$1 AND phase=$2 AND id NOT IN "
            "(SELECT id FROM stage_plan_history WHERE project_id=$1 AND phase=$2 ORDER BY created_at DESC LIMIT 20)",
            project_id, phase)

    async def latest_stage_plan_snapshot(self, project_id: str, phase: int) -> asyncpg.Record | None:
        assert self.pool
        return await self.pool.fetchrow(
            "SELECT * FROM stage_plan_history WHERE project_id=$1 AND phase=$2 ORDER BY created_at DESC LIMIT 1", project_id, phase)

    async def delete_stage_plan(self, project_id: str, phase: int) -> None:
        assert self.pool
        await self.pool.execute("DELETE FROM stage_plans WHERE project_id=$1 AND phase=$2", project_id, phase)

    async def set_stage_clarification(self, project_id: str, phase: int, clarification_json: str | None) -> None:
        """Store (or clear, with None) the pending structured clarification for a stage
        (D-108). Creates the plan row if absent; other columns keep their defaults."""
        assert self.pool
        await self.pool.execute(
            "INSERT INTO stage_plans (project_id, phase, clarification_json, updated_at) "
            "VALUES ($1,$2,$3, now()) "
            "ON CONFLICT (project_id, phase) DO UPDATE SET "
            "clarification_json=EXCLUDED.clarification_json, updated_at=now()",
            project_id, phase, clarification_json,
        )

    # ------------------------------------------------------------ generation feedback (D-57)
    async def insert_feedback(
        self, *, project_id: str, phase: int, source: str, category: str,
        severity: str, comment: str, rating: int | None = None,
        artefact_id: str | None = None, created_by: str | None = None,
    ) -> str:
        """A quality signal on a stage generation. source='human' (a person reports
        an issue) or source='validation' (the D-52 agent's verdict)."""
        assert self.pool
        feedback_id = new_id()
        await self.pool.execute(
            "INSERT INTO generation_feedback "
            "(id, project_id, phase, artefact_id, source, rating, category, severity, comment, created_by) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
            feedback_id, project_id, phase, artefact_id, source, rating,
            category, severity, comment, created_by,
        )
        return feedback_id

    async def list_feedback(self, project_id: str, phase: int | None = None) -> list[asyncpg.Record]:
        assert self.pool
        if phase is None:
            return await self.pool.fetch(
                "SELECT * FROM generation_feedback WHERE project_id=$1 ORDER BY created_at DESC",
                project_id,
            )
        return await self.pool.fetch(
            "SELECT * FROM generation_feedback WHERE project_id=$1 AND phase=$2 ORDER BY created_at DESC",
            project_id, phase,
        )

    async def replace_validation_feedback(
        self, *, project_id: str, phase: int, issues: list[dict[str, Any]],
    ) -> None:
        """Refresh the validation agent's verdict for a stage: drop the previous
        auto rows and insert the current ones so the surfaced result always
        reflects the latest generation (D-57)."""
        assert self.pool
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "DELETE FROM generation_feedback WHERE project_id=$1 AND phase=$2 AND source='validation'",
                project_id, phase,
            )
            for issue in issues:
                await conn.execute(
                    "INSERT INTO generation_feedback "
                    "(id, project_id, phase, source, category, severity, comment) "
                    "VALUES ($1,$2,$3,'validation',$4,$5,$6)",
                    new_id(), project_id, phase,
                    issue.get("category", "syntax"), issue.get("severity", "info"),
                    issue.get("comment", ""),
                )

    async def replace_source_feedback(
        self, *, project_id: str, phase: int, source: str, issues: list[dict[str, Any]],
    ) -> None:
        """Refresh one automatic source's rows for a stage (e.g. the gate-time 'security' review)."""
        assert self.pool
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("DELETE FROM generation_feedback WHERE project_id=$1 AND phase=$2 AND source=$3",
                               project_id, phase, source)
            for issue in issues:
                await conn.execute(
                    "INSERT INTO generation_feedback (id, project_id, phase, source, category, severity, comment) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7)",
                    new_id(), project_id, phase, source, issue.get("category", "security"),
                    issue.get("severity", "info"), issue.get("comment", ""))

    async def count_open_feedback(self, project_id: str, phase: int, source: str, categories: list[str]) -> int:
        assert self.pool
        return int(await self.pool.fetchval(
            "SELECT count(*) FROM generation_feedback WHERE project_id=$1 AND phase=$2 AND source=$3 "
            "AND status='open' AND category = ANY($4::text[])", project_id, phase, source, categories) or 0)

    async def get_feedback(self, feedback_id: str) -> asyncpg.Record | None:
        assert self.pool
        return await self.pool.fetchrow("SELECT * FROM generation_feedback WHERE id=$1", feedback_id)

    async def resolve_feedback(self, feedback_id: str, user_id: str | None) -> asyncpg.Record | None:
        assert self.pool
        return await self.pool.fetchrow(
            "UPDATE generation_feedback SET status='resolved', resolved_by=$2, resolved_at=now() "
            "WHERE id=$1 RETURNING *",
            feedback_id, user_id,
        )

    # ------------------------------------------------------------ audit index (D-09)
    async def insert_audit_index(self, row: dict[str, Any]) -> None:
        assert self.pool
        import json as _json
        body = row.get("body")
        await self.pool.execute(
            """
            INSERT INTO audit_index
              (id, project_id, phase, agent_role, event, provider, model,
               prompt_tokens, completion_tokens, artefact_hash, human_reviewer, s3_key, detail, body)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb,$14::jsonb)
            """,
            row["id"], row["project_id"], row.get("phase"), row["agent_role"], row["event"],
            row.get("provider"), row.get("model"), row.get("prompt_tokens"), row.get("completion_tokens"),
            row.get("artefact_hash"), row.get("human_reviewer"), row["s3_key"], row.get("detail", {}),
            (body if isinstance(body, str) else _json.dumps(body)) if body is not None else None,
        )

    async def list_audit(self, project_id: str) -> list[asyncpg.Record]:
        assert self.pool
        return await self.pool.fetch(
            "SELECT * FROM audit_index WHERE project_id=$1 ORDER BY timestamp DESC LIMIT 200", project_id
        )

    # Quality-metrics source: the validation scores + gate outcomes over time that
    # feed the quality dashboard (trend, first-pass rate, rework, flags).
    _QUALITY_EVENTS = (
        "ai.validation", "gate.approved", "gate.approved_override", "gate.amend_requested",
        "gate.pending_review", "stage.escalated", "downstream.stale_flagged",
    )

    async def list_quality_events(self, project_id: str) -> list[asyncpg.Record]:
        assert self.pool
        return await self.pool.fetch(
            "SELECT phase, event, timestamp, detail, agent_role FROM audit_index "
            "WHERE project_id=$1 AND event = ANY($2::text[]) ORDER BY timestamp ASC",
            project_id, list(self._QUALITY_EVENTS),
        )

    # ------------------------------------------------------------ artifact sign-offs
    async def record_artifact_signoff(
        self, *, id: str, project_id: str, phase: int, artefact_id: str, user_id: str, user_email: str,
    ) -> None:
        """Record one user's sign-off of one artifact. Idempotent per (artefact, user)."""
        assert self.pool
        await self.pool.execute(
            "INSERT INTO artifact_signoffs (id, project_id, phase, artefact_id, user_id, user_email) "
            "VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (artefact_id, user_id) DO NOTHING",
            id, project_id, phase, artefact_id, user_id, user_email,
        )

    async def list_phase_signoffs(self, project_id: str, phase: int) -> list[asyncpg.Record]:
        assert self.pool
        return await self.pool.fetch(
            "SELECT artefact_id, user_id, user_email, created_at FROM artifact_signoffs "
            "WHERE project_id=$1 AND phase=$2", project_id, phase,
        )

    async def clear_phase_signoffs(self, project_id: str, phase: int) -> None:
        """Drop a phase's sign-offs — the content changed (amend/retrigger), so prior
        reviews no longer apply."""
        assert self.pool
        await self.pool.execute(
            "DELETE FROM artifact_signoffs WHERE project_id=$1 AND phase=$2", project_id, phase,
        )

    # ---- review assignments (who reviews what: stage / type:<T> / artifact id) ----
    async def add_review_assignment(self, *, id: str, project_id: str, phase: int, target: str, user_email: str) -> None:
        assert self.pool
        await self.pool.execute(
            "INSERT INTO review_assignments (id, project_id, phase, target, user_email) "
            "VALUES ($1,$2,$3,$4,$5) ON CONFLICT (project_id, phase, target, user_email) DO NOTHING",
            id, project_id, phase, target, user_email,
        )

    async def remove_review_assignment(self, *, project_id: str, phase: int, target: str, user_email: str) -> None:
        assert self.pool
        await self.pool.execute(
            "DELETE FROM review_assignments WHERE project_id=$1 AND phase=$2 AND target=$3 AND user_email=$4",
            project_id, phase, target, user_email,
        )

    async def list_phase_assignments(self, project_id: str, phase: int) -> list[asyncpg.Record]:
        assert self.pool
        return await self.pool.fetch(
            "SELECT target, user_email FROM review_assignments WHERE project_id=$1 AND phase=$2",
            project_id, phase,
        )

    async def clear_phase_assignments(self, project_id: str, phase: int) -> None:
        assert self.pool
        await self.pool.execute(
            "DELETE FROM review_assignments WHERE project_id=$1 AND phase=$2", project_id, phase,
        )

    # ------------------------------------------------------------ chat transcript
    async def insert_chat_turn(self, session_id: str, phase: int, user_msg: str, assistant_msg: str) -> None:
        assert self.pool
        await self.pool.executemany(
            "INSERT INTO chat_messages (id, session_id, role, content, phase) VALUES ($1,$2,$3,$4,$5)",
            [
                (new_id(), session_id, "user", user_msg, phase),
                (new_id(), session_id, "assistant", assistant_msg, phase),
            ],
        )

    async def list_chat(self, session_id: str) -> list[asyncpg.Record]:
        assert self.pool
        return await self.pool.fetch(
            "SELECT * FROM chat_messages WHERE session_id=$1 ORDER BY created_at LIMIT 500", session_id
        )

    # ------------------------------------------------------------ workflow config (D-30)
    async def get_workflow(self, project_id: str) -> asyncpg.Record | None:
        assert self.pool
        return await self.pool.fetchrow(
            "SELECT config, version, updated_by, updated_at FROM project_workflows WHERE project_id=$1",
            project_id,
        )

    # ------------------------------------------------------------------ Canon + Formwork (D-38)
    async def list_canon(self, project_id: str, *, active_only: bool = True) -> list:
        assert self.pool
        sql = "SELECT * FROM project_canon WHERE project_id=$1"
        if active_only:
            sql += " AND active"
        sql += " ORDER BY CASE priority WHEN 'must' THEN 0 WHEN 'should' THEN 1 ELSE 2 END, created_at"
        return await self.pool.fetch(sql, project_id)

    async def insert_canon(self, *, project_id: str, category: str, priority: str, stage: int | None,
                           title: str, body: str, user_id: str) -> dict:
        assert self.pool
        row = await self.pool.fetchrow(
            """
            INSERT INTO project_canon (id, project_id, category, priority, stage, title, body, created_by)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING *
            """,
            new_id(), project_id, category, priority, stage, title, body, user_id,
        )
        return dict(row)

    async def update_canon(self, entry_id: str, project_id: str, patch: dict) -> dict | None:
        assert self.pool
        allowed = {"title", "body", "category", "priority", "stage", "active"}
        fields = {k: v for k, v in patch.items() if k in allowed}
        if not fields:
            row = await self.pool.fetchrow(
                "SELECT * FROM project_canon WHERE id=$1 AND project_id=$2", entry_id, project_id)
            return dict(row) if row else None
        sets = ", ".join(f"{k}=${i + 3}" for i, k in enumerate(fields))
        row = await self.pool.fetchrow(
            f"UPDATE project_canon SET {sets}, updated_at=now() WHERE id=$1 AND project_id=$2 RETURNING *",
            entry_id, project_id, *fields.values(),
        )
        return dict(row) if row else None

    async def delete_canon(self, entry_id: str, project_id: str) -> bool:
        assert self.pool
        res = await self.pool.execute(
            "DELETE FROM project_canon WHERE id=$1 AND project_id=$2", entry_id, project_id)
        return res.endswith("1")

    # ------------------------------------------------------------------ Project connections
    async def list_connections(self, project_id: str) -> list:
        assert self.pool
        return await self.pool.fetch("SELECT * FROM project_connections WHERE project_id=$1", project_id)

    async def get_connection(self, project_id: str, kind: str) -> dict | None:
        assert self.pool
        row = await self.pool.fetchrow("SELECT * FROM project_connections WHERE project_id=$1 AND kind=$2", project_id, kind)
        return dict(row) if row else None

    async def upsert_connection(self, project_id: str, kind: str, settings: dict, secret_enc: str | None, user: str) -> None:
        assert self.pool
        await self.pool.execute(
            """
            INSERT INTO project_connections (project_id, kind, settings, secret_enc, updated_by)
            VALUES ($1,$2,$3,$4,$5)
            ON CONFLICT (project_id, kind) DO UPDATE SET settings=$3, secret_enc=$4, updated_by=$5, updated_at=now(), last_test=NULL
            """, project_id, kind, settings, secret_enc, user)

    async def set_connection_test(self, project_id: str, kind: str, result: dict) -> None:
        assert self.pool
        await self.pool.execute("UPDATE project_connections SET last_test=$3 WHERE project_id=$1 AND kind=$2", project_id, kind, result)

    async def delete_connection(self, project_id: str, kind: str) -> None:
        assert self.pool
        await self.pool.execute("DELETE FROM project_connections WHERE project_id=$1 AND kind=$2", project_id, kind)

    async def count_kb_docs(self, scopes: list[str]) -> list:
        assert self.pool
        return await self.pool.fetch(
            "SELECT scope, source, count(*) AS n FROM kb_documents WHERE scope = ANY($1) GROUP BY scope, source", scopes)

    # ------------------------------------------------------------------ Memory
    async def list_memory(self, project_id: str, user_id: str) -> list:
        """Everything visible to this person on this project: the project's, the organisation's, their own."""
        assert self.pool
        return await self.pool.fetch(
            "SELECT * FROM project_memory WHERE (scope='project' AND project_id=$1) OR scope='org' "
            "OR (scope='user' AND owner_id=$2) ORDER BY CASE status WHEN 'suggested' THEN 0 WHEN 'active' THEN 1 ELSE 2 END, "
            "created_at DESC", project_id, user_id)

    async def get_memory(self, memory_id: str) -> dict | None:
        assert self.pool
        row = await self.pool.fetchrow("SELECT * FROM project_memory WHERE id=$1", memory_id)
        return dict(row) if row else None

    async def insert_memory(self, *, scope: str, project_id: str | None, owner_id: str | None, kind: str, title: str,
                            body: str, stage: int | None, status: str, source: dict, fingerprint: str,
                            created_by: str | None, reviewed_by: str | None = None) -> dict | None:
        """None when the same memory already exists (same fingerprint in the same scope)."""
        assert self.pool
        row = await self.pool.fetchrow(
            """
            INSERT INTO project_memory (id, scope, project_id, owner_id, kind, title, body, stage, status, source,
                                        fingerprint, created_by, reviewed_by, reviewed_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11,$12,$13, CASE WHEN $13::text IS NULL THEN NULL ELSE now() END)
            ON CONFLICT DO NOTHING RETURNING *
            """,
            new_id(), scope, project_id, owner_id, kind, title, body, stage, status, source,
            fingerprint, created_by, reviewed_by)
        return dict(row) if row else None

    async def update_memory(self, memory_id: str, patch: dict) -> dict | None:
        assert self.pool
        allowed = {"title", "body", "kind", "stage", "status", "scope", "reviewed_by", "reviewed_at", "fingerprint"}
        fields = {k: v for k, v in patch.items() if k in allowed}
        if not fields:
            return await self.get_memory(memory_id)
        sets = ", ".join(f"{k}=${i + 2}" for i, k in enumerate(fields))
        row = await self.pool.fetchrow(
            f"UPDATE project_memory SET {sets}, updated_at=now() WHERE id=$1 RETURNING *", memory_id, *fields.values())
        return dict(row) if row else None

    async def delete_memory(self, memory_id: str) -> bool:
        assert self.pool
        return (await self.pool.execute("DELETE FROM project_memory WHERE id=$1", memory_id)).endswith("1")

    async def touch_memory(self, ids: list[str]) -> None:
        assert self.pool
        if ids:
            await self.pool.execute(
                "UPDATE project_memory SET uses=uses+1, last_used_at=now() WHERE id = ANY($1::text[])", ids)

    async def list_formworks(self, project_id: str | None, *, include_platform: bool = True) -> list:
        assert self.pool
        if project_id and include_platform:
            return await self.pool.fetch(
                "SELECT * FROM formworks WHERE active AND (project_id=$1 OR project_id IS NULL) "
                "ORDER BY project_id NULLS LAST, artefact_type",
                project_id,
            )
        if project_id:
            return await self.pool.fetch(
                "SELECT * FROM formworks WHERE active AND project_id=$1 ORDER BY artefact_type", project_id)
        return await self.pool.fetch(
            "SELECT * FROM formworks WHERE active AND project_id IS NULL ORDER BY artefact_type")

    async def upsert_formwork(self, *, project_id: str | None, artefact_type: str, output_format: str,
                              name: str, template: str, analysis: dict, storage_key: str | None,
                              user_id: str) -> dict:
        """One active template per (scope, type, format) — re-uploading replaces."""
        assert self.pool
        async with self.pool.acquire() as conn, conn.transaction():
            if project_id is None:
                await conn.execute(
                    "UPDATE formworks SET active=false WHERE active AND project_id IS NULL "
                    "AND artefact_type=$1 AND output_format=$2", artefact_type, output_format)
            else:
                await conn.execute(
                    "UPDATE formworks SET active=false WHERE active AND project_id=$1 "
                    "AND artefact_type=$2 AND output_format=$3", project_id, artefact_type, output_format)
            row = await conn.fetchrow(
                """
                INSERT INTO formworks (id, project_id, artefact_type, output_format, name, template,
                                       analysis, storage_key, created_by)
                VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9) RETURNING *
                """,
                new_id(), project_id, artefact_type, output_format, name, template,
                analysis, storage_key, user_id,
            )
        return dict(row)

    async def deactivate_formwork(self, formwork_id: str) -> bool:
        assert self.pool
        res = await self.pool.execute("UPDATE formworks SET active=false WHERE id=$1", formwork_id)
        return res.endswith("1")

    async def get_formwork(self, formwork_id: str) -> dict | None:
        assert self.pool
        row = await self.pool.fetchrow("SELECT * FROM formworks WHERE id=$1", formwork_id)
        return dict(row) if row else None

    async def get_formworks_by_ids(self, ids: list[str]) -> list[asyncpg.Record]:
        """Resolve curated template @references (D-56). Platform-wide templates
        (project_id NULL) are shareable, so they resolve for any project."""
        assert self.pool
        if not ids:
            return []
        return await self.pool.fetch("SELECT * FROM formworks WHERE id = ANY($1::text[])", ids)

    # ------------------------------------------------------------------ observability (D-35)
    async def insert_trace(
        self, *, project_id: str | None, stage: int | None, kind: str,
        provider: str | None, model: str | None, tier: str | None, tag: str | None,
        prompt_tokens: int, completion_tokens: int, latency_ms: int,
        status: str, error: str | None, cost_usd: float,
        request_body: str | None = None, response_body: str | None = None,
    ) -> None:
        assert self.pool
        await self.pool.execute(
            """
            INSERT INTO llm_traces (id, project_id, stage, kind, provider, model, tier, tag,
                                    prompt_tokens, completion_tokens, latency_ms, status, error, cost_usd,
                                    request_body, response_body)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
            """,
            new_id(), project_id, stage, kind, provider, model, tier, tag,
            prompt_tokens, completion_tokens, latency_ms, status, error, cost_usd,
            request_body, response_body,
        )

    async def get_trace(self, trace_id: str) -> dict | None:
        """One trace row with the full request/response bodies (debug drill-down, D-104)."""
        assert self.pool
        row = await self.pool.fetchrow("SELECT * FROM llm_traces WHERE id=$1", trace_id)
        return dict(row) if row else None

    # ---- per-artifact generation parts (D-107 step 2) -------------------------
    async def upsert_generation_part(
        self, *, project_id: str, phase: int, field: str, status: str,
        error: str | None, value_json: str | None, partial_text: str | None = None,
    ) -> None:
        assert self.pool
        await self.pool.execute(
            """
            INSERT INTO generation_parts (project_id, phase, field, status, error, value_json, partial_text, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7, now())
            ON CONFLICT (project_id, phase, field) DO UPDATE
              SET status=EXCLUDED.status, error=EXCLUDED.error, value_json=EXCLUDED.value_json,
                  partial_text=EXCLUDED.partial_text, updated_at=now()
            """,
            project_id, phase, field, status, (error or None), value_json, partial_text,
        )

    async def list_generation_parts(self, project_id: str, phase: int) -> list[dict]:
        assert self.pool
        rows = await self.pool.fetch(
            "SELECT field, status, error, value_json, partial_text, updated_at FROM generation_parts "
            "WHERE project_id=$1 AND phase=$2 ORDER BY updated_at, field",
            project_id, phase,
        )
        return [dict(r) for r in rows]

    async def clear_generation_parts(self, project_id: str, phase: int) -> None:
        """A fresh full run starts from a clean slate (a resume or retrigger does not)."""
        assert self.pool
        await self.pool.execute(
            "DELETE FROM generation_parts WHERE project_id=$1 AND phase=$2", project_id, phase,
        )

    # ---- project traits: AI judgement + human overrides ("LLM decides, code enforces") ----
    async def get_stage_traits(self, project_id: str, phase: int) -> dict | None:
        assert self.pool
        row = await self.pool.fetchrow(
            "SELECT sig, traits_json FROM stage_traits WHERE project_id=$1 AND phase=$2", project_id, phase)
        return dict(row) if row else None

    async def upsert_stage_traits(self, project_id: str, phase: int, sig: str, traits_json: str) -> None:
        assert self.pool
        await self.pool.execute(
            """INSERT INTO stage_traits (project_id, phase, sig, traits_json, updated_at)
               VALUES ($1,$2,$3,$4, now())
               ON CONFLICT (project_id, phase) DO UPDATE
                 SET sig=EXCLUDED.sig, traits_json=EXCLUDED.traits_json, updated_at=now()""",
            project_id, phase, sig, traits_json)

    async def get_trait_overrides(self, project_id: str) -> dict[str, str]:
        assert self.pool
        rows = await self.pool.fetch(
            "SELECT trait, value FROM project_trait_overrides WHERE project_id=$1", project_id)
        return {r["trait"]: r["value"] for r in rows}

    async def set_trait_override(self, project_id: str, trait: str, value: str | None, by: str | None) -> None:
        """value None clears the override (back to the AI's judgement)."""
        assert self.pool
        if value is None:
            await self.pool.execute(
                "DELETE FROM project_trait_overrides WHERE project_id=$1 AND trait=$2", project_id, trait)
            return
        await self.pool.execute(
            """INSERT INTO project_trait_overrides (project_id, trait, value, updated_by, updated_at)
               VALUES ($1,$2,$3,$4, now())
               ON CONFLICT (project_id, trait) DO UPDATE
                 SET value=EXCLUDED.value, updated_by=EXCLUDED.updated_by, updated_at=now()""",
            project_id, trait, value, by)

    async def obs_summary(self, days: int) -> dict:
        assert self.pool
        totals = await self.pool.fetchrow(
            """
            SELECT count(*) FILTER (WHERE kind='llm')                          AS llm_calls,
                   count(*) FILTER (WHERE kind='tool')                         AS tool_calls,
                   coalesce(sum(prompt_tokens), 0)                             AS prompt_tokens,
                   coalesce(sum(completion_tokens), 0)                         AS completion_tokens,
                   coalesce(avg(latency_ms) FILTER (WHERE kind='llm'), 0)      AS avg_latency_ms,
                   coalesce(percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
                            FILTER (WHERE kind='llm'), 0)                      AS p95_latency_ms,
                   count(*) FILTER (WHERE status='error')                      AS errors,
                   coalesce(sum(cost_usd), 0)                                  AS cost_usd
            FROM llm_traces WHERE ts > now() - make_interval(days => $1)
            """,
            days,
        )
        providers = await self.pool.fetch(
            """
            SELECT provider, count(*) AS calls,
                   coalesce(sum(prompt_tokens), 0) AS prompt_tokens,
                   coalesce(sum(completion_tokens), 0) AS completion_tokens,
                   coalesce(avg(latency_ms), 0) AS avg_latency_ms,
                   count(*) FILTER (WHERE status='error') AS errors,
                   coalesce(sum(cost_usd), 0) AS cost_usd
            FROM llm_traces
            WHERE kind='llm' AND ts > now() - make_interval(days => $1)
            GROUP BY provider ORDER BY calls DESC
            """,
            days,
        )
        daily = await self.pool.fetch(
            """
            SELECT date_trunc('day', ts)::date AS day,
                   count(*) FILTER (WHERE kind='llm') AS llm_calls,
                   coalesce(sum(prompt_tokens + completion_tokens), 0) AS tokens
            FROM llm_traces WHERE ts > now() - make_interval(days => $1)
            GROUP BY 1 ORDER BY 1
            """,
            days,
        )
        tools = await self.pool.fetch(
            """
            SELECT tag AS tool, count(*) AS calls, coalesce(avg(latency_ms), 0) AS avg_latency_ms,
                   count(*) FILTER (WHERE status='error') AS errors
            FROM llm_traces
            WHERE kind='tool' AND ts > now() - make_interval(days => $1)
            GROUP BY tag ORDER BY calls DESC LIMIT 15
            """,
            days,
        )
        return {
            "days": days,
            "totals": dict(totals) if totals else {},
            "providers": [dict(r) for r in providers],
            "daily": [dict(r) for r in daily],
            "tools": [dict(r) for r in tools],
        }

    async def obs_recent(self, limit: int, project_id: str | None) -> list[dict]:
        assert self.pool
        # Explicit columns (NOT request_body/response_body): the debug bodies can be
        # large, so the list stays light and exposes only a has_bodies flag; the full
        # bodies are fetched per-row via get_trace (D-104).
        cols = (
            "id, ts, project_id, stage, kind, provider, model, tier, tag, "
            "prompt_tokens, completion_tokens, latency_ms, status, error, cost_usd, "
            "(request_body IS NOT NULL OR response_body IS NOT NULL) AS has_bodies"
        )
        if project_id:
            rows = await self.pool.fetch(
                f"SELECT {cols} FROM llm_traces WHERE project_id=$1 ORDER BY ts DESC LIMIT $2",
                project_id, limit,
            )
        else:
            rows = await self.pool.fetch(f"SELECT {cols} FROM llm_traces ORDER BY ts DESC LIMIT $1", limit)
        return [dict(r) for r in rows]

    async def upsert_workflow(self, project_id: str, config: dict, user_id: str) -> int:
        assert self.pool
        version = await self.pool.fetchval(
            """
            INSERT INTO project_workflows (project_id, config, version, updated_by)
            VALUES ($1, $2::jsonb, 1, $3)
            ON CONFLICT (project_id) DO UPDATE
              SET config=EXCLUDED.config, version=project_workflows.version + 1,
                  updated_by=EXCLUDED.updated_by, updated_at=now()
            RETURNING version
            """,
            project_id, config, user_id,
        )
        return int(version)

    # ------------------------------------------------------------ RAG documents (D-19)
    async def upsert_kb_doc(
        self, *, doc_id: str, scope: str, source: str, title: str, content: str, embedding: list[float]
    ) -> None:
        assert self.pool
        await self.pool.execute(
            """
            INSERT INTO kb_documents (id, scope, source, title, content, embedding)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (id) DO UPDATE
              SET title=EXCLUDED.title, content=EXCLUDED.content, embedding=EXCLUDED.embedding
            """,
            doc_id, scope, source, title, content, embedding,
        )

    # ------------------------------------------------------------ codebase (D-21)
    async def upsert_codebase_file(
        self, *, project_id: str, path: str, content: str, uploaded_by: str
    ) -> None:
        assert self.pool
        await self.pool.execute(
            """
            INSERT INTO codebase_files (id, project_id, path, content, size_bytes, uploaded_by)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (project_id, path) DO UPDATE
              SET content=EXCLUDED.content, size_bytes=EXCLUDED.size_bytes,
                  uploaded_by=EXCLUDED.uploaded_by, uploaded_at=now()
            """,
            new_id(), project_id, path, content, len(content.encode()), uploaded_by,
        )

    async def list_codebase_files(self, project_id: str) -> list[asyncpg.Record]:
        assert self.pool
        return await self.pool.fetch(
            "SELECT id, path, size_bytes, uploaded_at FROM codebase_files "
            "WHERE project_id=$1 ORDER BY path LIMIT 1000",
            project_id,
        )

    async def get_codebase_file(self, project_id: str, file_id: str) -> asyncpg.Record | None:
        assert self.pool
        return await self.pool.fetchrow(
            "SELECT * FROM codebase_files WHERE project_id=$1 AND id=$2", project_id, file_id
        )

    async def delete_codebase(self, project_id: str) -> int:
        """Remove the project's uploaded codebase: its files and the retrieval entries built from them."""
        async with self.pool.acquire() as conn:
            n = await conn.fetchval("SELECT count(*) FROM codebase_files WHERE project_id=$1", project_id)
            await conn.execute("DELETE FROM codebase_files WHERE project_id=$1", project_id)
            await conn.execute("DELETE FROM kb_documents WHERE scope=$1 AND source='codebase'", project_id)
        return int(n or 0)

    async def count_codebase_files(self, project_id: str) -> int:
        assert self.pool
        return await self.pool.fetchval(
            "SELECT count(*) FROM codebase_files WHERE project_id=$1", project_id
        ) or 0

    async def fetch_kb_docs(self, scopes: list[str]) -> list[asyncpg.Record]:
        assert self.pool
        return await self.pool.fetch(
            "SELECT id, scope, source, title, content, embedding FROM kb_documents WHERE scope = ANY($1) LIMIT 2000",
            scopes,
        )
