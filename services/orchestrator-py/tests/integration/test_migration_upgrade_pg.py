"""Upgrading a database that already holds waterfall data must be lossless and repeatable."""

import shutil
import uuid

import asyncpg
import pytest

from app.repos.pg import Database

from .conftest import ADMIN_DSN, MIGRATIONS

pytestmark = pytest.mark.asyncio


async def test_0028_upgrades_existing_waterfall_data_without_loss(tmp_path):
    pre = tmp_path / "pre"
    pre.mkdir()
    for f in MIGRATIONS.glob("*.sql"):
        if f.name < "0028":
            shutil.copy(f, pre / f.name)
    name = f"upg_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(ADMIN_DSN)
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    base = ADMIN_DSN.rpartition("/")[0]
    db = Database(f"{base}/{name}")
    await db.connect()
    try:
        await db.run_migrations(pre)                                   # the world before this feature
        await db.pool.execute("INSERT INTO users (id,email,display_name,role,password_hash) VALUES ('u','u@t','U','PROJECT_MANAGER','x')")
        await db.pool.execute("INSERT INTO projects (id,name,created_by,current_phase) VALUES ('p','Old','u',4)")
        await db.pool.execute("INSERT INTO artefacts (id,project_id,phase,type,title,content) VALUES ('a','p',6,'PRD','t','c')")
        await db.pool.execute("INSERT INTO notifications (id,project_id,phase,kind,title,roles) VALUES ('n','p',2,'stage_ready','t','[]'::jsonb)")
        with pytest.raises(asyncpg.CheckViolationError):               # the old cap really existed
            await db.pool.execute("UPDATE projects SET current_phase=300 WHERE id='p'")

        applied = await db.run_migrations(MIGRATIONS)
        assert applied == ["0028_agile.sql"]
        assert await db.run_migrations(MIGRATIONS) == []               # idempotent: nothing re-applied

        assert (await db.get_project("p"))["current_phase"] == 4        # data untouched
        assert await db.pool.fetchval("SELECT COUNT(*) FROM artefacts WHERE project_id='p'") == 1
        assert await db.pool.fetchval("SELECT kind FROM notifications WHERE id='n'") == "stage_ready"
        assert await db.get_project_agile("p") is None                 # a waterfall project has no agile row
        await db.pool.execute("UPDATE projects SET current_phase=300 WHERE id='p'")      # the cap is lifted
        with pytest.raises(asyncpg.CheckViolationError):                # but still bounded
            await db.pool.execute("UPDATE projects SET current_phase=100001 WHERE id='p'")
        with pytest.raises(asyncpg.CheckViolationError):                # and the notification kinds are still checked
            await db.pool.execute("INSERT INTO notifications (id,project_id,phase,kind,title,roles) VALUES ('x','p',1,'bogus','t','[]'::jsonb)")
    finally:
        await db.close()
        admin = await asyncpg.connect(ADMIN_DSN)
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()
