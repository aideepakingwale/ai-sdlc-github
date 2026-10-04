"""Authoring workspace for `.devmind/`.

Mirrors the repo layout under one key prefix in the content store (filesystem in local dev, S3 in
production). Files are written first and `manifest.json` LAST: a reader trusts only files the manifest
lists and verifies their hash, so a crash mid-write leaves the previous consistent index. Publishing to
the repo is a separate step (see publish.py); this class only tracks what is staged vs published."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .render import canonical_json, sha256
from .schema import MANIFEST_PATH, FileEntry, Manifest, Tier


class IndexStore(Protocol):
    async def put(self, key: str, body: str) -> None: ...
    async def get(self, key: str) -> str | None: ...
    async def delete(self, key: str) -> None: ...
    async def list_prefix(self, prefix: str) -> list[str]: ...


class IndexDriftError(Exception):
    """A workspace file is missing or its content no longer matches the manifest hash."""


@dataclass
class StagedFile:
    path: str
    content: str
    kind: str
    tier: Tier = Tier.ACTIVE
    release: str | None = None
    sprint: str | None = None
    stale: bool = False
    sourceCommit: str | None = None


@dataclass
class StageResult:
    written: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)


@dataclass
class PublishDiff:
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.added or self.modified or self.deleted)


@dataclass
class Drift:
    path: str
    kind: str  # missing | modified | orphan


class IndexWorkspace:
    def __init__(self, store: IndexStore, project_id: str) -> None:
        self._store = store
        self.project_id = project_id
        self._prefix = f"content-store/{project_id}/_devmind/"

    def key(self, path: str) -> str:
        return self._prefix + path

    # ------------------------------------------------------------------ manifest
    async def manifest(self) -> Manifest:
        raw = await self._store.get(self.key(MANIFEST_PATH))
        if not raw:
            return Manifest(projectId=self.project_id)
        return Manifest.model_validate_json(raw)

    async def _write_manifest(self, m: Manifest) -> None:
        await self._store.put(self.key(MANIFEST_PATH), canonical_json(m))

    # ------------------------------------------------------------------ reading
    async def read(self, path: str, *, verify: bool = True, manifest: Manifest | None = None) -> str:
        m = manifest or await self.manifest()
        entry = m.files.get(path)
        if entry is None:
            raise IndexDriftError(f"{path}: not listed in the manifest")
        body = await self._store.get(self.key(path))
        if body is None:
            raise IndexDriftError(f"{path}: missing from the workspace")
        if verify and sha256(body) != entry.sha256:
            raise IndexDriftError(f"{path}: content does not match the manifest hash (edited outside DevMind?)")
        return body

    async def read_optional(self, path: str, *, manifest: Manifest | None = None) -> str | None:
        m = manifest or await self.manifest()
        return await self.read(path, manifest=m) if path in m.files else None

    # ------------------------------------------------------------------ writing (single writer)
    async def stage(
        self, writes: list[StagedFile], removes: list[str] | None = None, *,
        current_release: str | None = None, current_sprint: str | None = None,
        force: set[str] | None = None,
    ) -> StageResult:
        """Write changed files, delete removed ones, then commit the new manifest last.
        `force` rewrites those paths even when the manifest hash already matches (repair of drift)."""
        m = await self.manifest()
        res = StageResult()
        files = dict(m.files)
        for w in writes:
            digest = sha256(w.content)
            old = files.get(w.path)
            meta_same = old is not None and (old.tier, old.release, old.sprint, old.stale, old.kind) == (
                w.tier, w.release, w.sprint, w.stale, w.kind)
            if old is not None and old.sha256 == digest and meta_same and w.path not in (force or set()):
                res.unchanged.append(w.path)
                continue
            if old is None or old.sha256 != digest or w.path in (force or set()):
                await self._store.put(self.key(w.path), w.content)
            files[w.path] = FileEntry(
                path=w.path, kind=w.kind, tier=w.tier, sha256=digest, bytes=len(w.content.encode("utf-8")),
                release=w.release, sprint=w.sprint, stale=w.stale, sourceCommit=w.sourceCommit or (old.sourceCommit if old else None),
            )
            res.written.append(w.path)
        for path in removes or []:
            if path in files:
                await self._store.delete(self.key(path))
                del files[path]
                res.removed.append(path)
        if res.written or res.removed or (current_release, current_sprint) != (m.currentRelease, m.currentSprint):
            m.files = dict(sorted(files.items()))
            m.currentRelease = current_release if current_release is not None else m.currentRelease
            m.currentSprint = current_sprint if current_sprint is not None else m.currentSprint
            m.generation += 1
            await self._write_manifest(m)  # LAST: the commit marker
        return res

    # ------------------------------------------------------------------ integrity
    async def verify(self) -> list[Drift]:
        m = await self.manifest()
        out: list[Drift] = []
        for path, e in m.files.items():
            body = await self._store.get(self.key(path))
            if body is None:
                out.append(Drift(path, "missing"))
            elif sha256(body) != e.sha256:
                out.append(Drift(path, "modified"))
        listed = {self.key(p) for p in m.files} | {self.key(MANIFEST_PATH)}
        for key in await self._store.list_prefix(self._prefix):
            if key not in listed:
                out.append(Drift(key[len(self._prefix):], "orphan"))
        return sorted(out, key=lambda d: (d.path, d.kind))

    async def purge_orphans(self, drift: list[Drift]) -> list[str]:
        """Delete files nobody listed (stray hand-made files or leftovers of an interrupted write)."""
        gone = [d.path for d in drift if d.kind == "orphan"]
        for p in gone:
            await self._store.delete(self.key(p))
        return gone

    # ------------------------------------------------------------------ publish state
    async def pending_publish(self) -> PublishDiff:
        m = await self.manifest()
        d = PublishDiff()
        for path, e in m.files.items():
            if path not in m.publishedFiles:
                d.added.append(path)
            elif m.publishedFiles[path] != e.sha256:
                d.modified.append(path)
        d.deleted = sorted(p for p in m.publishedFiles if p not in m.files)
        d.added.sort()
        d.modified.sort()
        return d

    async def mark_published(
        self, commit_sha: str, *, files: dict[str, str] | None = None, deleted: list[str] | None = None,
    ) -> None:
        """Record what the commit contained. With no arguments the whole workspace is marked published; with a
        snapshot only those files are (the workspace may have changed since the commit was prepared)."""
        m = await self.manifest()
        m.publishedCommit = commit_sha
        if files is None:
            m.publishedFiles = {p: e.sha256 for p, e in m.files.items()}
        else:
            merged = dict(m.publishedFiles)
            merged.update(files)
            for d in deleted or []:
                merged.pop(d, None)
            m.publishedFiles = merged
        await self._write_manifest(m)

    async def publishable_manifest(self) -> str:
        """The manifest as committed to the repo: publish-state fields are blanked (a file cannot contain
        the hash of the commit that contains it), so the committed copy is deterministic."""
        m = await self.manifest()
        m.publishedCommit = None
        m.publishedFiles = {}
        return canonical_json(m)
