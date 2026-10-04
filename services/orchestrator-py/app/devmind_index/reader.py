"""Typed, hash-verified reads from the workspace (resolves archived sprints transparently)."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from .schema import (
    LOOKUP_PATH,
    Charter,
    Manifest,
    ReleaseIndex,
    SprintDigest,
    Tier,
    archive_path,
    charter_path,
    release_path,
)
from .workspace import IndexWorkspace


def seq(ident: str) -> int:
    """Numeric order of ids like S-014 (sortable regardless of padding)."""
    m = re.search(r"(\d+)\s*$", ident)
    return int(m.group(1)) if m else 0


class ArchiveFile(BaseModel):
    release: str
    sprints: list[SprintDigest] = Field(default_factory=list)


class IndexReader:
    """Reads against ONE manifest snapshot (loaded once), so a packet costs O(files read), not O(n²)."""

    def __init__(self, ws: IndexWorkspace) -> None:
        self.ws = ws
        self._m: Manifest | None = None

    async def manifest(self) -> Manifest:
        if self._m is None:
            self._m = await self.ws.manifest()
        return self._m

    async def _opt(self, path: str) -> str | None:
        m = await self.manifest()
        return await self.ws.read(path, manifest=m) if path in m.files else None

    async def charter(self) -> Charter | None:
        raw = await self._opt(charter_path())
        return Charter.model_validate_json(raw) if raw else None

    async def releases(self) -> list[ReleaseIndex]:
        m = await self.manifest()
        out = [ReleaseIndex.model_validate_json(await self.ws.read(p, manifest=m))
               for p, e in m.files.items() if e.kind == "release"]
        return sorted(out, key=lambda r: r.id)

    async def release(self, release_id: str) -> ReleaseIndex | None:
        raw = await self._opt(release_path(release_id))
        return ReleaseIndex.model_validate_json(raw) if raw else None

    async def release_tiers(self) -> dict[str, Tier]:
        m = await self.manifest()
        return {e.release: e.tier for e in m.files.values() if e.kind == "release" and e.release}

    async def archive(self, release_id: str) -> ArchiveFile | None:
        raw = await self._opt(archive_path(release_id))
        return ArchiveFile.model_validate_json(raw) if raw else None

    async def sprint_ids(self) -> list[str]:
        """All known sprint ids (live + archived), oldest first — from the lookup, no file scan."""
        return sorted((await self.lookup()).get("sprints", {}), key=seq)

    async def sprint(self, sprint_id: str) -> tuple[SprintDigest, bool] | None:
        """(digest, from_archive). Archived digests are the compact form."""
        loc = (await self.lookup()).get("sprints", {}).get(sprint_id)
        if not loc:
            return None
        raw = await self._opt(loc["path"])
        if not raw:
            return None
        if loc["tier"] == Tier.ARCHIVED.value:
            for sd in ArchiveFile.model_validate_json(raw).sprints:
                if sd.id == sprint_id:
                    return sd, True
            return None
        return SprintDigest.model_validate_json(raw), False

    async def lookup(self) -> dict[str, Any]:
        import json
        if not hasattr(self, "_lk"):
            raw = await self._opt(LOOKUP_PATH)
            self._lk = json.loads(raw) if raw else {
                "stories": {}, "components": {}, "decisions": {}, "keywords": {}, "sprints": {}, "releases": {}}
        return self._lk
