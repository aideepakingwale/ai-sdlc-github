"""Builds the index from structured inputs: renders files, assigns tiers, rolls archived releases into
one file each, rebuilds the lookup and stages everything through the workspace (single writer)."""

from __future__ import annotations

import re

from .reader import ArchiveFile, IndexReader, seq
from .render import canonical_json, charter_md, readme_md, release_md, sprint_md
from .schema import (
    LOOKUP_PATH,
    README_PATH,
    ROOT,
    Charter,
    ReleaseIndex,
    SprintDigest,
    Tier,
    archive_path,
    charter_path,
    release_path,
    sprint_path,
)
from .workspace import IndexDriftError, IndexWorkspace, StagedFile, StageResult

_WORD = re.compile(r"[a-z][a-z0-9]{3,}")
_STOP = {"with", "from", "that", "this", "into", "when", "will", "have", "should", "user", "users", "page"}
MAX_KEYWORDS = 3000
MAX_PER_KEYWORD = 20


def compact(d: SprintDigest) -> SprintDigest:
    """What survives archiving: enough to answer 'what happened and why', nothing bulky."""
    c = d.model_copy(deep=True)
    c.deltas, c.openIssues = [], []
    for s in c.stories:
        s.artifacts = []
    for x in c.decisions:
        x.rationale = x.rationale[:120]
    c.summary = c.summary[:300]
    return c


def tier_for_rank(rank: int, closed: bool, *, active_releases: int = 2, closed_keep: int = 2) -> Tier:
    """rank 0 = newest release. Only a CLOSED release can leave the active/closed tiers."""
    if rank < active_releases:
        return Tier.ACTIVE
    if rank < active_releases + closed_keep or not closed:
        return Tier.CLOSED
    return Tier.ARCHIVED


class IndexBuilder:
    def __init__(self, ws: IndexWorkspace, *, active_releases: int = 2, closed_keep: int = 2) -> None:
        self.ws = ws
        self.reader = IndexReader(ws)
        self.active_releases = active_releases
        self.closed_keep = closed_keep

    async def update(
        self, *, charter: Charter | None = None, sprint: SprintDigest | None = None,
        release: ReleaseIndex | None = None, current_release: str | None = None,
        current_sprint: str | None = None, source_commit: str | None = None, heal: bool = False,
    ) -> StageResult:
        """Apply new inputs and restage. `heal=True` additionally verifies every file and rewrites any
        that drifted (hand-edited, missing, half-written); leave it off on the hot path."""
        r = self.reader
        force: set[str] = set()
        if heal:
            drift = await self.ws.verify()
            force = {d.path for d in drift if d.kind in ("modified", "missing")}
            await self.ws.purge_orphans(drift)
        m = await self.ws.manifest()
        # Existing state is read with hash verification. A drifted file is tolerated only when this call
        # supplies its replacement; otherwise there is nothing trustworthy to rebuild it from.
        drifted: list[tuple[str, str]] = []

        async def load(path: str, kind: str, ident: str):
            try:
                return await self.ws.read(path)
            except IndexDriftError:
                drifted.append((kind, ident))
                force.add(path)
                return None

        if charter is None:
            raw = await load(charter_path(), "charter", "charter") if charter_path() in m.files else None
            charter = Charter.model_validate_json(raw) if raw else None
        releases: dict[str, ReleaseIndex] = {}
        digests: dict[str, SprintDigest] = {}
        archived_prev: dict[str, SprintDigest] = {}
        for p, e in m.files.items():
            if e.kind == "release":
                raw = await load(p, "release", e.release or p)
                if raw:
                    releases[e.release or p] = ReleaseIndex.model_validate_json(raw)
            elif e.kind == "sprint" and p.endswith("digest.json"):
                raw = await load(p, "sprint", e.sprint or p)
                if raw:
                    digests[e.sprint or p] = SprintDigest.model_validate_json(raw)
            elif e.kind == "archive" and e.release:
                raw = await load(p, "archive", e.release)
                if raw:
                    for sd in ArchiveFile.model_validate_json(raw).sprints:
                        archived_prev[sd.id] = sd
        if release:
            releases[release.id] = release
        if sprint:
            digests[sprint.id] = sprint
            rel = releases.get(sprint.release)
            if rel and sprint.id not in rel.sprints:
                rel.sprints = sorted([*rel.sprints, sprint.id], key=seq)
        supplied = {("charter", "charter")} if charter else set()
        supplied |= {("release", release.id)} if release else set()
        supplied |= {("sprint", sprint.id)} if sprint else set()
        lost = [d for d in drifted if d not in supplied]
        if lost:
            raise IndexDriftError(
                "cannot rebuild from drifted files without their sources: " + ", ".join(f"{k} {i}" for k, i in lost)
                + " — restore them from the last published repo ref")

        order = sorted(releases, reverse=True)  # newest first (ids sort chronologically)
        tiers = {rid: tier_for_rank(i, releases[rid].closed, active_releases=self.active_releases,
                                    closed_keep=self.closed_keep) for i, rid in enumerate(order)}

        writes: list[StagedFile] = []
        removes: list[str] = []
        sprint_tier: dict[str, tuple[Tier, str]] = {}  # sprint id -> (tier, path) for the lookup

        if charter:
            writes.append(StagedFile(charter_path(), canonical_json(charter), "charter", sourceCommit=source_commit))
            writes.append(StagedFile(f"{ROOT}/charter.md", charter_md(charter), "charter-md", sourceCommit=source_commit))
        writes.append(StagedFile(README_PATH, readme_md(), "readme"))

        for rid, rel in releases.items():
            t = tiers[rid]
            writes.append(StagedFile(release_path(rid), canonical_json(rel), "release", t, release=rid, sourceCommit=source_commit))
            writes.append(StagedFile(f"{ROOT}/releases/{rid}/index.md", release_md(rel), "release-md", t, release=rid, sourceCommit=source_commit))

        by_release: dict[str, list[SprintDigest]] = {}
        for d in [*archived_prev.values(), *digests.values()]:
            by_release.setdefault(d.release, []).append(d)
        seen: set[str] = set()
        for rid, group in by_release.items():
            uniq = {d.id: d for d in group}  # live digest wins over its archived copy
            ordered = sorted(uniq.values(), key=lambda d: seq(d.id))
            t = tiers.get(rid, Tier.CLOSED)
            if t is Tier.ARCHIVED:
                arch = ArchiveFile(release=rid, sprints=[compact(d) for d in ordered])
                writes.append(StagedFile(archive_path(rid), canonical_json(arch), "archive", t, release=rid, sourceCommit=source_commit))
                for d in ordered:
                    sprint_tier[d.id] = (t, archive_path(rid))
                    for p in (sprint_path(d.id), f"{ROOT}/sprints/{d.id}/digest.md"):
                        if p in m.files:
                            removes.append(p)
                    seen.add(d.id)
            else:
                for d in ordered:
                    if d.id in archived_prev and d.id not in digests:
                        continue  # was archived earlier; a release never un-archives
                    writes.append(StagedFile(sprint_path(d.id), canonical_json(d), "sprint", t, release=rid, sprint=d.id, sourceCommit=source_commit))
                    writes.append(StagedFile(f"{ROOT}/sprints/{d.id}/digest.md", sprint_md(d), "sprint-md", t, release=rid, sprint=d.id, sourceCommit=source_commit))
                    sprint_tier[d.id] = (t, sprint_path(d.id))
                    seen.add(d.id)

        writes.append(StagedFile(LOOKUP_PATH, canonical_json(self._lookup(by_release, sprint_tier, releases, tiers)), "lookup"))
        return await self.ws.stage(
            writes, sorted(set(removes)), force=force,
            current_release=current_release or (order[0] if order else None),
            current_sprint=current_sprint or (sprint.id if sprint else None),
        )

    @staticmethod
    def _lookup(by_release: dict[str, list[SprintDigest]], sprint_tier: dict[str, tuple[Tier, str]],
                releases: dict[str, ReleaseIndex], tiers: dict[str, Tier]) -> dict:
        sprints = {sid: {"release": next((r for r, g in by_release.items() if any(x.id == sid for x in g)), ""),
                         "tier": t.value, "path": path} for sid, (t, path) in sprint_tier.items()}
        rels = {rid: {"name": r.name, "goal": r.goal[:100], "tier": tiers[rid].value, "closed": r.closed}
                for rid, r in releases.items()}
        stories: dict[str, dict] = {}
        comps: dict[str, list[str]] = {}
        decisions: dict[str, str] = {}
        kw: dict[str, list[str]] = {}
        for rid, group in by_release.items():
            for d in {x.id: x for x in group}.values():
                tier, path = sprint_tier.get(d.id, (Tier.CLOSED, sprint_path(d.id)))
                for s in d.stories:
                    stories[s.id] = {"sprint": d.id, "release": rid, "tier": tier.value, "path": path}
                    for c in s.components:
                        comps.setdefault(c, [])
                        if d.id not in comps[c]:
                            comps[c].append(d.id)
                    for w in set(_WORD.findall(s.title.lower())) - _STOP:
                        kw.setdefault(w, []).append(s.id)
                for x in d.decisions:
                    decisions[x.id] = path
                    for c in x.components:
                        comps.setdefault(c, [])
                        if d.id not in comps[c]:
                            comps[c].append(d.id)
        for c in comps:
            comps[c] = sorted(comps[c], key=seq)
        # bounded: most useful keywords first, each capped — the lookup must stay small at any project age
        ranked = sorted(kw.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:MAX_KEYWORDS]
        kw_out = {k: sorted(v, key=seq)[-MAX_PER_KEYWORD:] for k, v in sorted(ranked)}
        return {"sprints": dict(sorted(sprints.items())), "releases": dict(sorted(rels.items())),
                "stories": dict(sorted(stories.items())), "components": dict(sorted(comps.items())),
                "decisions": dict(sorted(decisions.items())), "keywords": kw_out}
