"""Import organisation rule packs from a folder of YAML files into the database.

    python scripts/import_org_packs.py packs/organisation/aviation            # validate and import
    python scripts/import_org_packs.py packs/organisation/aviation --check    # validate only

Reads DATABASE_URL like the orchestrator. Packs are validated the way the Rule packs screen validates them (tags from the profile
vocabulary, included packs must exist, no loops between sets). A pack that is already in the organisation's library at the same or a newer
version is left alone; a newer file replaces it. The same files can also be pasted into Governance -> Organisation -> Rule packs.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.rule_packs import PackError, builtin_packs, normalise_pack, resolve_entries  # noqa: E402


def load(folder: Path) -> list[dict]:
    files = sorted(folder.glob("*.yaml"))
    if not files:
        raise SystemExit(f"No .yaml files in {folder}")
    raw = []
    for f in files:
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        if str(data.get("id") or "") != f.stem:
            raise SystemExit(f"{f.name}: id must be '{f.stem}'")
        raw.append(data)
    known = {p["id"] for p in builtin_packs()} | {d["id"] for d in raw}
    packs = []
    for data in raw:
        try:
            packs.append(normalise_pack(data, known_ids=known))
        except ValueError as err:
            raise SystemExit(f"{data['id']}: {err}") from err
    catalog = {p["id"]: p for p in builtin_packs()} | {p["id"]: p for p in packs}
    for p in packs:
        try:
            resolve_entries(catalog, p["id"])
        except (PackError, Exception) as err:  # noqa: BLE001
            raise SystemExit(f"{p['id']}: {err}") from err
    return packs


async def run(folder: Path, check: bool, user_id: str) -> None:
    packs = load(folder)
    print(f"{len(packs)} packs valid: {', '.join(p['id'] for p in packs)}")
    if check:
        return
    from app.repos.pg import Database

    db = Database(os.environ["DATABASE_URL"])
    await db.connect()
    try:
        have = {r["id"]: r for r in await db.list_org_packs()}
        for p in packs:
            if p["id"] in have and int(have[p["id"]]["version"]) >= p["version"]:
                print(f"  kept     {p['id']} (version {have[p['id']]['version']} is already there)")
                continue
            await db.upsert_org_pack({**p, "active": True}, user_id)
            print(f"  imported {p['id']} v{p['version']}")
    finally:
        await db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--check", action="store_true", help="validate only, do not touch the database")
    ap.add_argument("--user", default="system", help="recorded as the author (default: system)")
    a = ap.parse_args()
    asyncio.run(run(a.folder, a.check, a.user))
