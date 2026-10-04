"""Pure iteration engine — no I/O.

The base workflow config describes ONE sprint's worth of stages (scope "iteration") and one release's
worth (scope "release") next to the one-off project stages. This module materialises them:

* every stage of every sprint is a normal stage SLOT with its own `seq`, so gates, artifacts, plans, parts
  and sign-offs work unchanged;
* sprint 1 / release 1 reuse the base slot numbers (so numbering matches the template); later sprints and
  releases get fresh slots above everything allocated so far;
* sprint N's entry stage depends on sprint N-1's closing stage, which makes sprints strictly sequential;
* a release-scoped stage depends on the closing stage of the release's last sprint.

Everything is deterministic: the same inputs always give the same expanded view.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SEP = "@"


def instance_key(base_key: str, label: str) -> str:
    return f"{base_key}{SEP}{label}"


@dataclass(frozen=True)
class IterationRef:
    id: str
    number: int
    label: str
    release_id: str
    status: str  # planned | active | closed | cancelled


@dataclass(frozen=True)
class ReleaseRef:
    id: str
    number: int
    code: str
    status: str  # open | hardening | closed


@dataclass(frozen=True)
class InstanceRef:
    seq: int
    key: str
    base_key: str
    scope: str  # iteration | release
    iteration_id: str | None
    release_id: str | None


@dataclass(frozen=True)
class Block:
    """The iteration block of a base workflow: its stages in dependency order, the single entry
    stage (the one the previous sprint feeds) and the single closing stage."""
    keys: tuple[str, ...]
    entry: str
    sink: str


def iteration_block(stages: list[dict[str, Any]]) -> Block | None:
    block = [s for s in stages if s.get("scope") == "iteration"]
    if not block:
        return None
    keys = {s["key"] for s in block}
    entries = [s for s in block if not any(d in keys for d in s.get("dependsOn", []))]
    sinks = [s for s in block if not any(s["key"] in o.get("dependsOn", []) for o in block)]
    if len(entries) != 1 or len(sinks) != 1:  # validate_workflow guarantees this; fail loudly if bypassed
        raise ValueError("iteration block must have exactly one entry and one closing stage")
    ordered = sorted(block, key=lambda s: s["seq"])
    return Block(tuple(s["key"] for s in ordered), entries[0]["key"], sinks[0]["key"])


def allocate_seqs(
    base_stages: list[dict[str, Any]], existing: list[InstanceRef], *, scope: str, first: bool,
) -> dict[str, int]:
    """Slots for a new sprint/release: base key -> seq. The first sprint/release reuses the base slots;
    later ones take the next free numbers above every base slot and every slot already allocated."""
    todo = sorted((s for s in base_stages if s.get("scope") == scope), key=lambda s: s["seq"])
    if first:
        return {s["key"]: s["seq"] for s in todo}
    top = max([s["seq"] for s in base_stages] + [i.seq for i in existing] + [0])
    return {s["key"]: top + n for n, s in enumerate(todo, start=1)}


def _levels(stages: list[dict[str, Any]]) -> list[list[int]]:
    """Topological levels of the expanded graph (stages mutated with their `level`). A dependency on a
    stage that is not materialised is ignored, so a half-built sprint never blocks forever."""
    seqs = [s["seq"] for s in stages]
    if len(set(seqs)) != len(seqs):
        raise ValueError("expanded workflow has duplicate stage slots")
    keys = {s["key"] for s in stages}
    deps = {s["key"]: {d for d in s["dependsOn"] if d in keys} for s in stages}
    order = sorted(stages, key=lambda s: s["seq"])
    done: set[str] = set()
    levels: list[list[int]] = []
    while len(done) < len(stages):
        ready = [s for s in order if s["key"] not in done and deps[s["key"]] <= done]
        if not ready:
            raise ValueError("expanded workflow has a dependency cycle")
        for s in ready:
            s["level"] = len(levels)
        done |= {s["key"] for s in ready}
        levels.append([s["seq"] for s in ready])
    return levels


def expand(
    base: dict[str, Any], *, iterations: list[IterationRef], releases: list[ReleaseRef],
    instances: list[InstanceRef],
) -> dict[str, Any]:
    """Expand the derived base view with the materialised sprints/releases."""
    base_stages: list[dict[str, Any]] = base["stages"]
    block = iteration_block(base_stages)
    inst_by = {(i.base_key, i.iteration_id or i.release_id): i for i in instances}
    iters = sorted((i for i in iterations if i.status != "cancelled"), key=lambda i: i.number)
    rel_by_id = {r.id: r for r in releases}
    out: list[dict[str, Any]] = []

    for s in base_stages:
        if s.get("scope", "project") == "project":
            out.append({**s, "baseKey": s["key"], "iteration": None, "iterationLabel": None, "release": None})

    sink_of: dict[str, str] = {}  # iteration id -> its closing stage's instance key
    prev: IterationRef | None = None
    for it in iters:
        rel = rel_by_id.get(it.release_id)
        for s in sorted((x for x in base_stages if x.get("scope") == "iteration"), key=lambda x: x["seq"]):
            inst = inst_by.get((s["key"], it.id))
            if inst is None:
                continue
            deps: list[str] = []
            for d in s["dependsOn"]:
                dep_stage = next((x for x in base_stages if x["key"] == d), None)
                if dep_stage is not None and dep_stage.get("scope") == "iteration":
                    deps.append(instance_key(d, it.label))
                else:
                    deps.append(d)
            if block and s["key"] == block.entry and prev is not None:
                deps.append(sink_of[prev.id])  # sprints are strictly sequential
            out.append({
                **s, "key": inst.key, "baseKey": s["key"], "seq": inst.seq, "dependsOn": deps,
                "name": f"{s['name']} · {it.label}", "iteration": it.number, "iterationLabel": it.label,
                "iterationId": it.id, "release": rel.code if rel else None,
            })
            if block and s["key"] == block.sink:
                sink_of[it.id] = inst.key
        prev = it

    for rel in sorted(releases, key=lambda r: r.number):
        last_it = max((i for i in iters if i.release_id == rel.id), key=lambda i: i.number, default=None)
        for s in sorted((x for x in base_stages if x.get("scope") == "release"), key=lambda x: x["seq"]):
            inst = inst_by.get((s["key"], rel.id))
            if inst is None:
                continue
            deps = []
            for d in s["dependsOn"]:
                dep_stage = next((x for x in base_stages if x["key"] == d), None)
                if dep_stage is not None and dep_stage.get("scope") == "iteration":
                    if last_it is not None:
                        deps.append(instance_key(d, last_it.label))
                else:
                    deps.append(d)
            if last_it is not None and last_it.id in sink_of and sink_of[last_it.id] not in deps:
                deps.append(sink_of[last_it.id])
            out.append({
                **s, "key": inst.key, "baseKey": s["key"], "seq": inst.seq, "dependsOn": deps,
                "name": f"{s['name']} · {rel.code}", "iteration": None, "iterationLabel": None,
                "release": rel.code, "releaseId": rel.id,
            })

    out.sort(key=lambda s: s["seq"])
    levels = _levels(out)
    return {"stages": out, "levels": levels}
