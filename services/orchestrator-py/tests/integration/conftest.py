"""Integration fixtures against a REAL Postgres. Set TEST_DATABASE_URL (admin DSN, e.g.
postgresql://postgres@localhost:5433/postgres) to enable; otherwise these tests are skipped."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import asyncpg
import pytest

from app.repos.pg import Database

ADMIN_DSN = os.environ.get("TEST_DATABASE_URL")
MIGRATIONS = Path(__file__).resolve().parents[4] / "infra" / "migrations"


def pytest_collection_modifyitems(config, items):
    if ADMIN_DSN:
        return
    skip = pytest.mark.skip(reason="TEST_DATABASE_URL not set (real-Postgres integration tests)")
    for item in items:
        if "integration" in item.nodeid:
            item.add_marker(skip)


@pytest.fixture
async def pg():
    name = f"agile_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(ADMIN_DSN)
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    base, _, _ = ADMIN_DSN.rpartition("/")
    db = Database(f"{base}/{name}")
    await db.connect()
    try:
        applied = await db.run_migrations(MIGRATIONS)
        assert "0029_release_lineages.sql" in applied
        yield db
    finally:
        await db.close()
        admin = await asyncpg.connect(ADMIN_DSN)
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


@pytest.fixture
async def project(pg):
    """A user + project the agile rows can hang off."""
    assert pg.pool
    uid = "u-" + uuid.uuid4().hex[:8]
    await pg.pool.execute(
        "INSERT INTO users (id, email, display_name, role, password_hash) VALUES ($1,$2,'T','PROJECT_MANAGER','x')",
        uid, f"{uid}@t.local")
    pid = "p-" + uuid.uuid4().hex[:8]
    await pg.pool.execute(
        "INSERT INTO projects (id, name, created_by) VALUES ($1,'Proj',$2)", pid, uid)
    return pid, uid


@pytest.fixture
async def env(pg):
    from .helpers import build_env

    return await build_env(pg)
