"""Run-to-completion entrypoints: `python -m app.scripts migrate|seed`."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from .auth.passwords import hash_password
from .config import get_settings
from .repos.pg import Database

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"

# Mirrors infra/keycloak/sdlc-realm.json.
SEED_USERS = [
    ("superadmin@sdlc.local", "Platform SuperAdmin", "SUPER_ADMIN"),
    ("pm@sdlc.local", "Parker Manager", "PROJECT_MANAGER"),
    ("po@sdlc.local", "Priya Owner", "PO"),
    ("sa@sdlc.local", "Sol Architect", "SA"),
    ("ta@sdlc.local", "Tech Architect", "TA"),
    ("qa@sdlc.local", "Quinn Assurance", "QA"),
    ("devops@sdlc.local", "Devi Ops", "DEVOPS"),
    ("dev@sdlc.local", "Dev Eloper", "DEV"),
]


async def migrate() -> None:
    db = Database(get_settings().DATABASE_URL)
    await db.connect()
    try:
        applied = await db.run_migrations(MIGRATIONS_DIR)
        print(f"Applied migrations: {', '.join(applied)}" if applied else "Migrations up to date")
    finally:
        await db.close()


async def seed() -> None:
    import os

    db = Database(get_settings().DATABASE_URL)
    await db.connect()
    try:
        password_hash = hash_password(os.environ.get("SEED_PASSWORD", "Password123!"))
        for email, name, role in SEED_USERS:
            await db.seed_user(email=email, display_name=name, role=role, password_hash=password_hash)
        print(f"Seeded {len(SEED_USERS)} users (superadmin@sdlc.local .. dev@sdlc.local)")
    finally:
        await db.close()


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "migrate":
        asyncio.run(migrate())
    elif command == "seed":
        asyncio.run(seed())
    else:
        print("usage: python -m app.scripts migrate|seed")
        sys.exit(1)
