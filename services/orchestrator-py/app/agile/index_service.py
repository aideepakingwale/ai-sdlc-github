"""IndexService — keeps the `.devmind/` project memory up to date and publishes it.

Everything is staged in the authoring workspace (filesystem in dev, S3 in production) while a stage is being
generated, and committed to the repository by ONE atomic `github_commit_index` call when that stage's gate is
approved (the existing deferred-publish path). Readers (context packets) use the workspace, never GitHub."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, AsyncIterator, Callable

from ..devmind_index.builder import IndexBuilder
from ..devmind_index.packet import Budget, Packet, Query, assemble
from ..devmind_index.publish import Strategy, build_publish_action, target_branch
from ..devmind_index.reader import IndexReader
from ..devmind_index.schema import Charter, Decision, ReleaseIndex, SprintDigest, Story, Tier
from ..devmind_index.workspace import IndexWorkspace
from .specs import (
    DesignDelta, SpecChange, group_by_component, merge_delta, spec_path,
)

log = logging.getLogger("agile")

DEFAULT_DOD = [
    "Acceptance criteria are met",
    "Automated tests pass",
    "Reviewed and accepted by the Product Owner",
    "Documentation and living specs are updated",
]
LockFactory = Callable[[str], Any]


class _LocalLocks:
    """One asyncio lock per project (single orchestrator instance). Production wires a Redis lock."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def __call__(self, project_id: str) -> asyncio.Lock:
        return self._locks.setdefault(project_id, asyncio.Lock())


def redis_lock_factory(redis: Any, *, timeout: int = 120, blocking_timeout: int = 60) -> LockFactory:
    """Cross-instance single-writer lock (redis-py's Lock: token-owned, auto-expiring)."""
    return lambda project_id: redis.lock(f"devmind:index:{project_id}", timeout=timeout, blocking_timeout=blocking_timeout)


class IndexService:
    def __init__(
        self, db: Any, content: Any, audit: Any, publisher: Any, workflow: Any, *,
        default_branch: str = "main", locks: LockFactory | None = None,
    ) -> None:
        self._db, self._content, self._audit, self._publisher, self._workflow = db, content, audit, publisher, workflow
        self._default_branch = default_branch
        self._locks: LockFactory = locks or _LocalLocks()
        if publisher is not None:
            publisher.on_result("github_commit_index", self._on_commit_result)

    # ------------------------------------------------------------------ plumbing
    def workspace(self, project_id: str) -> IndexWorkspace:
        return IndexWorkspace(self._content, project_id)

    @contextlib.asynccontextmanager
    async def _locked(self, project_id: str) -> AsyncIterator[None]:
        async with self._locks(project_id):
            yield

    async def _strategy(self, project_id: str) -> Strategy:
        cfg = await self._db.get_project_agile(project_id)
        return (cfg["index_strategy"] if cfg else "index-branch")  # type: ignore[return-value]

    async def _text(self, art: Any) -> str:
        if art["content"]:
            return art["content"]
        key = art["storage_key"] if "storage_key" in art.keys() else None
        return (await self._content.get(key) or "") if key else ""

    # ------------------------------------------------------------------ builders (pure-ish: read DB, return models)
    async def build_charter(self, project_id: str) -> Charter:
        project = await self._db.get_project(project_id)
        arts = await self._db.list_artefacts(project_id)
        prd = next((a for a in arts if a["type"] == "PRD"), None)
        vision = ""
        if prd is not None:
            body = await self._text(prd)
            vision = " ".join(body.replace("#", " ").split())[:600]
        canon = [c for c in await self._db.list_canon(project_id) if c["priority"] == "must"]
        return Charter(
            name=project["name"], vision=vision, techStack=project.get("tech_stack") or "",
            constraints=[c["title"] for c in canon][:8], definitionOfDone=DEFAULT_DOD,
            keyDecisions=[c["title"] for c in await self._db.list_canon(project_id) if c["category"] == "decision"][:10])

    async def build_digest(self, project_id: str, iteration: Any) -> SprintDigest:
        items = await self._db.list_backlog(project_id, iteration_id=iteration["id"])
        summary_row = iteration["summary"] or {}
        if iteration["status"] == "closed":
            # After close the items have left the sprint: rebuild from the recorded outcome.
            ids = set(summary_row.get("doneItemIds", [])) | set(summary_row.get("carriedItemIds", []))
            items = [r for r in await self._db.list_backlog(project_id) if r["id"] in ids]
        done_ids = set(summary_row.get("doneItemIds", []))
        stories = [
            Story(id=r["item_key"], title=r["title"][:200],
                  status="done" if (r["status"] == "done" or r["id"] in done_ids) else "carried",
                  jiraKey=r["jira_key"], components=list(r["components"] or [])[:10])
            for r in sorted(items, key=lambda r: r["rank"]) if r["type"] != "epic"]
        decisions = await self._sprint_decisions(project_id, iteration)
        done = [s for s in stories if s.status == "done"]
        pts = sum(float(r["estimate"] or 0) for r in items if r["status"] == "done" or r["id"] in done_ids)
        carried = [s.id for s in stories if s.status != "done"]
        rel = await self._db.get_release(iteration["release_id"])
        return SprintDigest(
            id=iteration["label"], release=rel["code"], goal=(iteration["goal"] or "")[:240], stories=stories,
            decisions=decisions, deltas=[], openIssues=[f"{k} carried over" for k in carried][:10],
            summary=(f"{len(done)} of {len(stories)} stories done ({pts:g} points)."
                     + (f" Carried over: {', '.join(carried[:8])}." if carried else ""))[:590])

    async def _sprint_decisions(self, project_id: str, iteration: Any) -> list[Decision]:
        wf = await self._workflow.view(project_id)
        build = next((s for s in wf["stages"] if s.get("iterationLabel") == iteration["label"]
                      and s.get("agileRole") == "build"), None)
        row = await self._db.latest_proposal(project_id, build["seq"], "delta") if build else None
        if not row:
            return []
        out = []
        for n, c in enumerate(row["payload"].get("changes", [])[:12], start=1):
            out.append(Decision(
                id=f"D-{iteration['label']}-{n}", title=f"{c['op']} {c['component']} / {c['section']}"[:160],
                rationale=(c.get("rationale") or "")[:300], components=[c["component"]]))
        return out

    async def build_release(self, project_id: str, release: Any, *, closed: bool) -> ReleaseIndex:
        its = [i for i in await self._db.list_iterations(project_id) if i["release_id"] == release["id"]
               and i["status"] != "cancelled"]
        ws = self.workspace(project_id)
        manifest = await ws.manifest()
        pointers = {
            e.path.rsplit("/", 1)[-1][:-3]: f"{e.path}@{e.sha256[:12]}"
            for e in manifest.files.values() if e.kind == "spec"}
        decisions: list[Decision] = []
        for it in its[-4:]:
            decisions += (await self._sprint_decisions(project_id, it))[:3]
        backlog = await self._db.list_backlog(project_id)
        open_items = [f"{r['item_key']} {r['title']}"[:120] for r in backlog
                      if r["status"] in ("ready", "refined") and r["type"] != "epic"][:8]
        done_pts = sum(float((i["summary"] or {}).get("velocity", 0)) for i in its)
        return ReleaseIndex(
            id=release["code"], name=release["name"], goal=(release["goal"] or "")[:300],
            sprints=[i["label"] for i in its], closed=closed, decisions=decisions[:10], specPointers=pointers,
            openItems=open_items,
            summary=f"{len(its)} sprint(s), {done_pts:g} points delivered."[:790])

    # ------------------------------------------------------------------ staging
    async def stage_project(self, project_id: str, *, actor: str = "system") -> list[str]:
        async with self._locked(project_id):
            res = await IndexBuilder(self.workspace(project_id)).update(charter=await self.build_charter(project_id))
            return res.written

    async def stage_sprint(self, project_id: str, iteration_id: str) -> list[str]:
        it = await self._db.get_iteration(iteration_id)
        if it is None:
            return []
        async with self._locked(project_id):
            rel = await self._db.get_release(it["release_id"])
            digest = await self.build_digest(project_id, it)
            release = await self.build_release(project_id, rel, closed=rel["status"] == "closed")
            if digest.id not in release.sprints:
                release.sprints = sorted([*release.sprints, digest.id])
            res = await IndexBuilder(self.workspace(project_id)).update(
                charter=await self.build_charter(project_id), sprint=digest, release=release,
                current_release=release.id, current_sprint=digest.id)
            return res.written

    async def stage_release(self, project_id: str, release_id: str, *, closed: bool) -> list[str]:
        rel = await self._db.get_release(release_id)
        if rel is None:
            return []
        async with self._locked(project_id):
            release = await self.build_release(project_id, rel, closed=closed)
            res = await IndexBuilder(self.workspace(project_id)).update(
                charter=await self.build_charter(project_id), release=release, current_release=release.id)
            return res.written

    async def apply_delta(self, project_id: str, delta: DesignDelta) -> list[dict[str, Any]]:
        """Merge a sprint's design delta into the living specs (staged, unpublished). Returns per-change results."""
        results: list[dict[str, Any]] = []
        async with self._locked(project_id):
            ws = self.workspace(project_id)
            manifest = await ws.manifest()
            specs: dict[str, str] = {}
            for comp, changes in group_by_component(delta.changes).items():
                path = spec_path(comp)
                current = await ws.read(path) if path in manifest.files else None
                new, res = merge_delta(current, comp, changes)
                results += res
                if new != current and any(r["status"] == "applied" for r in res):
                    specs[path] = new
            if specs:
                await IndexBuilder(ws).update(specs=specs)
        return results

    # ------------------------------------------------------------------ publishing
    async def queue_publish(self, project_id: str, phase: int, *, open_pr: bool = False) -> bool:
        """Queue the commit (and optionally the release PR) to run when this stage's gate is approved."""
        if self._publisher is None:
            return False
        strategy = await self._strategy(project_id)
        ws = self.workspace(project_id)
        action = await build_publish_action(ws, strategy=strategy, default_branch=self._default_branch)
        if action is None and not open_pr:
            return False
        if action is not None:
            await self._publisher.add_action(project_id, phase, action, replace_tools=("github_commit_index",))
        if open_pr and strategy == "index-branch":
            m = await ws.manifest()
            await self._publisher.add_action(project_id, phase, {
                "tool": "github_open_pull_request",
                "args": {"head": target_branch(strategy), "base": self._default_branch, "reuseExisting": True,
                         "title": f"DevMind index — {m.currentRelease or 'release'}",
                         "body": "Release close: merges the generated `.devmind/` project memory. Generated by DevMind; "
                                 "do not edit these files by hand."},
                "stub": {}}, replace_tools=("github_open_pull_request",))
        return True

    async def _on_commit_result(self, project_id: str, phase: int, action: dict[str, Any], result: dict[str, Any]) -> None:
        snap = (action.get("stub") or {}).get("snapshot") or {}
        sha = result.get("commitSha")
        if not sha:
            return
        await self.workspace(project_id).mark_published(
            sha, files=snap, deleted=(action.get("stub") or {}).get("deletions") or [])
        self._audit.record(project_id=project_id, phase=phase, agent_role="Index", event="index.published",
                           detail={"commit": sha, "files": len(snap), "noop": bool(result.get("noop")),
                                   "branch": action["args"]["branch"]})

    # ------------------------------------------------------------------ reading
    async def packet(self, project_id: str, *, story_ids: tuple[str, ...] = (), components: tuple[str, ...] = (),
                     keywords: tuple[str, ...] = (), budget: Budget | None = None) -> Packet | None:
        ws = self.workspace(project_id)
        if not (await ws.manifest()).files:
            return None
        return await assemble(ws, Query(story_ids, components, keywords), budget)

    async def status(self, project_id: str) -> dict[str, Any]:
        ws = self.workspace(project_id)
        m = await ws.manifest()
        if not m.files:
            return {"initialised": False}
        diff = await ws.pending_publish()
        tiers: dict[str, int] = {}
        for e in m.files.values():
            tiers[e.tier.value] = tiers.get(e.tier.value, 0) + 1
        drift = await ws.verify()
        return {
            "initialised": True, "generation": m.generation, "files": len(m.files), "tiers": tiers,
            "currentRelease": m.currentRelease, "currentSprint": m.currentSprint,
            "publishedCommit": m.publishedCommit,
            "unpublished": {"added": len(diff.added), "modified": len(diff.modified), "deleted": len(diff.deleted)},
            "drift": [{"path": d.path, "kind": d.kind} for d in drift][:20],
            "specs": sorted(e.path.rsplit("/", 1)[-1][:-3] for e in m.files.values() if e.kind == "spec"),
            "strategy": await self._strategy(project_id),
        }

    async def reader(self, project_id: str) -> IndexReader:
        return IndexReader(self.workspace(project_id))


__all__ = ["IndexService", "redis_lock_factory", "DEFAULT_DOD", "Tier"]
