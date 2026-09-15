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
        """JIT provisioning: local row for FK integrity + audit."""
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

    # ------------------------------------------------------------ projects & sessions
    async def create_project(
        self, *, name: str, created_by: str, tech_stack: str = "Node.js + TypeScript",
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

    async def update_project_integrations(self, project_id: str, integrations: dict[str, Any]) -> None:
        """Edit a project's GitHub/Atlassian targets after creation."""
        assert self.pool
        await self.pool.execute(
            "UPDATE projects SET github_repo=$2, atlassian_site_url=$3, "
            "jira_project_key=$4, confluence_space_key=$5 WHERE id=$1",
            project_id, integrations.get("githubRepo"), integrations.get("atlassianSiteUrl"),
            integrations.get("jiraProjectKey"), integrations.get("confluenceSpaceKey"),
        )

    async def delete_project(self, project_id: str) -> bool:
        """Delete a project and its Postgres rows. Tables with an ON DELETE
        CASCADE FK go automatically once the project row is removed (migration 0010
        fixed sessions + artefacts). `llm_traces` carries a project_id with no FK,
        so purge it explicitly. `audit_index` is deliberately NOT deleted — it is
        append-only by trigger and an audit trail must survive the deletion
        of what it describes (non-repudiation); it has no FK, so retained rows
        don't block the delete. Runs in one transaction. Returns False if absent."""
        assert self.pool
        async with self.pool.acquire() as conn, conn.transaction():
            exists = await conn.fetchval("SELECT 1 FROM projects WHERE id=$1", project_id)
            if not exists:
                return False
            await conn.execute("DELETE FROM llm_traces WHERE project_id=$1", project_id)
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

    # ------------------------------------------------------------ members
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
    ) -> str:
        """When storage_key is set, the body lives in the content-store tier
 and only a short pointer/preview is kept in the `content` column."""
        assert self.pool
        artefact_id = artefact_id or new_id()
        db_content = content if storage_key is None else content[:2000]
        await self.pool.execute(
            "INSERT INTO artefacts (id, project_id, phase, type, title, content, url, storage_key, storage_mode) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
            artefact_id, project_id, phase, type_, title, db_content, url, storage_key, storage_mode,
        )
        return artefact_id

    async def list_artefacts(self, project_id: str) -> list[asyncpg.Record]:
        assert self.pool
        return await self.pool.fetch(
            "SELECT * FROM artefacts WHERE project_id=$1 ORDER BY created_at DESC LIMIT 500", project_id
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
        """Replace an artifact's body in place and bump its version ( diagram
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
        """Back-patch artifact URLs after deferred publish: every artifact
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
        """Remove a stage's artifacts (on retrigger); returns deleted rows
        (with storage_key) so the caller can purge the content-store too."""
        assert self.pool
        return await self.pool.fetch(
            "DELETE FROM artefacts WHERE project_id=$1 AND phase=$2 RETURNING id, storage_key",
            project_id, phase,
        )

    # ------------------------------------------------------------ notifications
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

    async def mark_notification_read(self, notification_id: str, user_id: str) -> None:
        """Append the user to read_by (idempotent — `?` is jsonb array containment)."""
        assert self.pool
        await self.pool.execute(
            "UPDATE notifications SET read_by = read_by || to_jsonb($2::text) "
            "WHERE id=$1 AND NOT (read_by ? $2)",
            notification_id, user_id,
        )

    # ------------------------------------------------------------ stage attachments
    async def insert_attachment(
        self, *, project_id: str, phase: int, filename: str, content_type: str,
        size_bytes: int, is_text: bool, storage_key: str, created_by: str | None,
        attachment_id: str | None = None,
    ) -> str:
        assert self.pool
        # Caller may supply the id so the DB row, the content-store key and the
        # id returned to the client all agree.
        attachment_id = attachment_id or new_id()
        await self.pool.execute(
            "INSERT INTO stage_attachments "
            "(id, project_id, phase, filename, content_type, size_bytes, is_text, storage_key, created_by) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
            attachment_id, project_id, phase, filename, content_type, size_bytes,
            is_text, storage_key, created_by,
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

    async def delete_attachment(self, attachment_id: str) -> asyncpg.Record | None:
        assert self.pool
        return await self.pool.fetchrow(
            "DELETE FROM stage_attachments WHERE id=$1 RETURNING id, storage_key", attachment_id
        )

    async def get_artefacts_by_ids(self, ids: list[str]) -> list[asyncpg.Record]:
        """Resolve a curated set of artifact @references for injection."""
        assert self.pool
        if not ids:
            return []
        return await self.pool.fetch("SELECT * FROM artefacts WHERE id = ANY($1::text[])", ids)

    # ------------------------------------------------------------ stage plan drafts
    async def get_stage_plan(self, project_id: str, phase: int) -> asyncpg.Record | None:
        assert self.pool
        return await self.pool.fetchrow(
            "SELECT * FROM stage_plans WHERE project_id=$1 AND phase=$2", project_id, phase
        )

    async def upsert_stage_plan(
        self, *, project_id: str, phase: int, prompt_overlay: str,
        referenced_artifact_ids: list[str], attachment_ids: list[str],
        formwork_ids: list[str], origin: str, updated_by: str | None,
        step_overrides: dict | None = None,
    ) -> None:
        assert self.pool
        import json as _json
        await self.pool.execute(
            "INSERT INTO stage_plans "
            "(project_id, phase, prompt_overlay, referenced_artifact_ids, attachment_ids, formwork_ids, "
            " step_overrides, origin, updated_by, updated_at) "
            "VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,$6::jsonb,$7::jsonb,$8,$9, now()) "
            "ON CONFLICT (project_id, phase) DO UPDATE SET "
            "prompt_overlay=EXCLUDED.prompt_overlay, referenced_artifact_ids=EXCLUDED.referenced_artifact_ids, "
            "attachment_ids=EXCLUDED.attachment_ids, formwork_ids=EXCLUDED.formwork_ids, "
            "step_overrides=EXCLUDED.step_overrides, "
            "origin=EXCLUDED.origin, updated_by=EXCLUDED.updated_by, updated_at=now()",
            project_id, phase, prompt_overlay, referenced_artifact_ids, attachment_ids,
            formwork_ids, _json.dumps(step_overrides or {}), origin, updated_by,
        )

    async def delete_stage_plan(self, project_id: str, phase: int) -> None:
        assert self.pool
        await self.pool.execute("DELETE FROM stage_plans WHERE project_id=$1 AND phase=$2", project_id, phase)

    # ------------------------------------------------------------ generation feedback
    async def insert_feedback(
        self, *, project_id: str, phase: int, source: str, category: str,
        severity: str, comment: str, rating: int | None = None,
        artefact_id: str | None = None, created_by: str | None = None,
    ) -> str:
        """A quality signal on a stage generation. source='human' (a person reports
        an issue) or source='validation' (the agent's verdict)."""
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
        reflects the latest generation."""
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

    # ------------------------------------------------------------ audit index
    async def insert_audit_index(self, row: dict[str, Any]) -> None:
        assert self.pool
        await self.pool.execute(
            """
            INSERT INTO audit_index
              (id, project_id, phase, agent_role, event, provider, model,
               prompt_tokens, completion_tokens, artefact_hash, human_reviewer, s3_key, detail)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb)
            """,
            row["id"], row["project_id"], row.get("phase"), row["agent_role"], row["event"],
            row.get("provider"), row.get("model"), row.get("prompt_tokens"), row.get("completion_tokens"),
            row.get("artefact_hash"), row.get("human_reviewer"), row["s3_key"], row.get("detail", {}),
        )

    async def list_audit(self, project_id: str) -> list[asyncpg.Record]:
        assert self.pool
        return await self.pool.fetch(
            "SELECT * FROM audit_index WHERE project_id=$1 ORDER BY timestamp DESC LIMIT 200", project_id
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

    # ------------------------------------------------------------ workflow config
    async def get_workflow(self, project_id: str) -> asyncpg.Record | None:
        assert self.pool
        return await self.pool.fetchrow(
            "SELECT config, version, updated_by, updated_at FROM project_workflows WHERE project_id=$1",
            project_id,
        )

    # ------------------------------------------------------------------ Canon + Formwork
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
        """Resolve curated template @references. Platform-wide templates
        (project_id NULL) are shareable, so they resolve for any project."""
        assert self.pool
        if not ids:
            return []
        return await self.pool.fetch("SELECT * FROM formworks WHERE id = ANY($1::text[])", ids)

    # ------------------------------------------------------------------ observability
    async def insert_trace(
        self, *, project_id: str | None, stage: int | None, kind: str,
        provider: str | None, model: str | None, tier: str | None, tag: str | None,
        prompt_tokens: int, completion_tokens: int, latency_ms: int,
        status: str, error: str | None, cost_usd: float,
    ) -> None:
        assert self.pool
        await self.pool.execute(
            """
            INSERT INTO llm_traces (id, project_id, stage, kind, provider, model, tier, tag,
                                    prompt_tokens, completion_tokens, latency_ms, status, error, cost_usd)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
            """,
            new_id(), project_id, stage, kind, provider, model, tier, tag,
            prompt_tokens, completion_tokens, latency_ms, status, error, cost_usd,
        )

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
        if project_id:
            rows = await self.pool.fetch(
                "SELECT * FROM llm_traces WHERE project_id=$1 ORDER BY ts DESC LIMIT $2",
                project_id, limit,
            )
        else:
            rows = await self.pool.fetch("SELECT * FROM llm_traces ORDER BY ts DESC LIMIT $1", limit)
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

    # ------------------------------------------------------------ RAG documents
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

    # ------------------------------------------------------------ codebase
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
