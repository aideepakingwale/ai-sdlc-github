"""Integrity checks: hashes, pointers and references. Returns human-readable problems (empty = healthy)."""

from __future__ import annotations

from .reader import IndexReader
from .workspace import IndexWorkspace


async def check_integrity(ws: IndexWorkspace) -> list[str]:
    problems = [f"{d.kind}: {d.path}" for d in await ws.verify()]
    r = IndexReader(ws)
    releases = {x.id: x for x in await r.releases()}
    sprint_ids = set(await r.sprint_ids())
    lk = await r.lookup()
    for rid, rel in releases.items():
        for sid in rel.sprints:
            if sid not in sprint_ids:
                problems.append(f"release {rid} lists unknown sprint {sid}")
    for sid in sorted(sprint_ids):
        got = await r.sprint(sid)
        if got and got[0].release not in releases:
            problems.append(f"sprint {sid} refers to unknown release {got[0].release}")
    for story, loc in lk["stories"].items():
        if loc["sprint"] not in sprint_ids:
            problems.append(f"lookup story {story} points at missing sprint {loc['sprint']}")
    files = (await ws.manifest()).files
    for dec, path in lk["decisions"].items():
        if path not in files:
            problems.append(f"lookup decision {dec} points at missing file {path}")
    return problems
