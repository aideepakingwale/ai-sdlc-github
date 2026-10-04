"""Budgeted context packet. An agent never receives history — it receives a bounded packet assembled for
the task: charter (always) + release layer + a few relevant sprint digests + spec pointers.
Deterministic and model-free: relevance comes from the lookup, the budget is enforced by code."""

from __future__ import annotations

from dataclasses import dataclass, field

from .reader import IndexReader, seq
from .render import charter_md, est_tokens, release_md, sprint_md
from .workspace import IndexWorkspace


@dataclass(frozen=True)
class Budget:
    charter: int = 2000
    releases: int = 2000
    sprints: int = 2500
    specs: int = 1500

    @property
    def total(self) -> int:
        return self.charter + self.releases + self.sprints + self.specs


@dataclass(frozen=True)
class Query:
    story_ids: tuple[str, ...] = ()
    components: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()


@dataclass
class PacketItem:
    layer: str
    ref: str
    tokens: int
    why: str


@dataclass
class Packet:
    text: str
    tokens: int
    items: list[PacketItem] = field(default_factory=list)
    dropped: list[PacketItem] = field(default_factory=list)


def _fit(text: str, limit: int) -> str:
    if est_tokens(text) <= limit:
        return text
    return text[: max(0, limit * 4 - 40)].rstrip() + "\n…(trimmed to budget)\n"


async def assemble(ws: IndexWorkspace, query: Query | None = None, budget: Budget | None = None) -> Packet:
    q, b = query or Query(), budget or Budget()
    r = IndexReader(ws)
    items: list[PacketItem] = []
    dropped: list[PacketItem] = []
    parts: list[str] = []

    # L0 — charter: always, fixed
    ch = await r.charter()
    if ch:
        t = _fit(charter_md(ch), b.charter)
        parts.append("## PROJECT CHARTER\n" + t)
        items.append(PacketItem("L0", "charter", est_tokens(t), "always"))

    # L1 — release layer: the current release in full (or trimmed), the rest as one line each (from the lookup)
    lk = await r.lookup()
    m = await r.manifest()
    rel_ids = sorted(lk["releases"], reverse=True)
    cur_id = m.currentRelease if m.currentRelease in lk["releases"] else (rel_ids[0] if rel_ids else None)
    cur = await r.release(cur_id) if cur_id else None
    lines: list[str] = []
    if cur:
        lines.append(_fit(release_md(cur), b.releases // 2 + b.releases // 4))
    used = est_tokens("\n".join(lines))
    for rid in rel_ids:
        if rid == cur_id:
            continue
        info = lk["releases"][rid]
        line = f"- {rid} {info['name']} [{info['tier']}]: {info['goal']}"
        if used + est_tokens(line) > b.releases:
            dropped.append(PacketItem("L1", rid, est_tokens(line), "budget"))
            continue
        lines.append(line)
        used += est_tokens(line)
    if lines:
        txt = "\n".join(lines)
        parts.append("## RELEASES\n" + txt)
        items.append(PacketItem("L1", cur_id or "releases", est_tokens(txt), "current release + index"))

    # L2 — sprint digests: previous sprint always, then relevant ones (story > component > keyword > recency)
    all_sprints = await r.sprint_ids()
    prev = [s for s in all_sprints if s != m.currentSprint][-1:]
    ranked: dict[str, tuple[int, int]] = {}

    def want(sid: str, rank: int) -> None:
        cur_rank = ranked.get(sid, (9, 0))[0]
        if rank < cur_rank:
            ranked[sid] = (rank, -seq(sid))

    for sid in prev:
        want(sid, 0)
    for st in q.story_ids:
        if st in lk["stories"]:
            want(lk["stories"][st]["sprint"], 0)
    for c in q.components:
        for sid in reversed(lk["components"].get(c, [])[-5:]):
            want(sid, 1)
    for k in q.keywords:
        for st in lk["keywords"].get(k.lower(), []):
            if st in lk["stories"]:
                want(lk["stories"][st]["sprint"], 2)
    why = {0: "previous sprint / requested story", 1: "touches requested component", 2: "keyword match"}
    used = 0
    sp_parts: list[str] = []
    for sid, (rank, _) in sorted(ranked.items(), key=lambda kv: (kv[1][0], kv[1][1], kv[0])):
        got = await r.sprint(sid)
        if not got:
            continue
        digest, from_archive = got
        txt = sprint_md(digest)
        if est_tokens(txt) > b.sprints // 3:
            txt = _fit(txt, b.sprints // 3)
        tk = est_tokens(txt)
        item = PacketItem("L2", sid + (" (archived)" if from_archive else ""), tk, why[rank])
        if used + tk > b.sprints:
            dropped.append(PacketItem(item.layer, item.ref, tk, "budget"))
            continue
        sp_parts.append(txt)
        items.append(item)
        used += tk
    if sp_parts:
        parts.append("## SPRINT DIGESTS\n" + "\n\n".join(sp_parts))

    # specs — pointers only (never bodies); the agent retrieves sections on demand
    ptr: list[str] = []
    for rid in ([cur_id] if cur_id else []) + [x for x in rel_ids if x != cur_id][:1]:
        rel = cur if rid == cur_id else await r.release(rid)
        for comp in q.components:
            ref = rel.specPointers.get(comp) if rel else None
            if ref and not any(p.startswith(f"- {comp} →") for p in ptr):
                ptr.append(f"- {comp} → {ref}")
    if ptr:
        txt = _fit("\n".join(ptr), b.specs)
        parts.append("## LIVING-SPEC POINTERS\n" + txt)
        items.append(PacketItem("L3", "spec-pointers", est_tokens(txt), "requested components"))

    text = "\n\n".join(parts)
    return Packet(text=text, tokens=est_tokens(text), items=items, dropped=dropped)
