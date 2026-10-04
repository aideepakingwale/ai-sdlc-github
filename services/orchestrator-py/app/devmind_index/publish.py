"""Turns what is staged-but-unpublished into ONE publish action for the deferred publish queue.
Strategy B (default): commit to a dedicated `devmind/index` branch; strategy A: the default branch."""

from __future__ import annotations

from typing import Any, Literal

from .render import sha256
from .schema import MANIFEST_PATH
from .workspace import IndexWorkspace

Strategy = Literal["index-branch", "default-branch"]
INDEX_BRANCH = "devmind/index"


def target_branch(strategy: Strategy, default_branch: str = "main") -> str:
    return INDEX_BRANCH if strategy == "index-branch" else default_branch


async def build_publish_action(
    ws: IndexWorkspace, *, strategy: Strategy = "index-branch", default_branch: str = "main", message: str | None = None,
) -> dict[str, Any] | None:
    diff = await ws.pending_publish()
    if diff.empty:
        return None
    files = [{"path": p, "content": await ws.read(p)} for p in [*diff.added, *diff.modified]]
    files.append({"path": MANIFEST_PATH, "content": await ws.publishable_manifest()})
    m = await ws.manifest()
    return {
        "tool": "github_commit_index",   # atomic multi-file commit; added to the connector in a later step
        "args": {
            "branch": target_branch(strategy, default_branch),
            "files": files, "deletions": diff.deleted,
            "message": message or f"chore(devmind): update index (generation {m.generation}) [skip ci]",
            "strategy": strategy,
        },
        # Not sent to the connector: lets the caller record exactly what this commit contained.
        "stub": {"snapshot": {f["path"]: sha256(f["content"]) for f in files if f["path"] != MANIFEST_PATH},
                 "deletions": list(diff.deleted)},
    }
