"""The carry set: the slice of context a forked release takes over from the release it was forked from.

A forked release starts as an EMPTY plate. Only what is chosen here is copied in, by value, and recorded in the
release's `fork.json` with where it came from and a hash (the "merge base"). Nothing is ever merged back, and the
source release can change or be archived without touching this one.

What can be carried: living-spec sections, requirements (epics and delivered stories), decisions and retro learnings.
Everything in this module is deterministic and model-free except `CarryService.suggest`, where a model may RANK the
candidates; code decides what is valid, in budget and allowed."""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from ..devmind_index.builder import IndexBuilder
from ..devmind_index.render import canonical_json, carried_md
from ..devmind_index.schema import (
    CarriedFrom,
    CarriedItem,
    ForkRecord,
    carried_path,
    fork_path,
    release_spec_path,
)
from ..devmind_index.workspace import StagedFile
from ..domain.errors import SdlcError
from .specs import MAX_SPEC_BYTES, parse_spec, render_spec, section_hash

log = logging.getLogger("agile")

MAX_ITEMS = 40                  # entries in one release's carry set
MAX_BYTES = 60_000              # total carried text
MAX_TEXT = 8_000                # one entry
_WORD = re.compile(r"[a-z][a-z0-9]{3,}")
_STOP = {"with", "from", "that", "this", "into", "when", "will", "have", "should", "user", "users", "page", "need", "which"}
KIND_WEIGHT = {"spec-section": 1.0, "decision": 0.8, "requirement": 0.6, "learning": 0.4}


# ------------------------------------------------------------------ pure rules
def tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower())) - _STOP


def score(item: CarriedItem, scope: set[str], components: set[str]) -> float:
    """Relevance of one candidate to the new release's scope: keyword overlap, plus a bonus for a spec section of
    a component the scope names. Deterministic; ties break on kind, then id."""
    overlap = len(tokens(f"{item.title} {item.text}") & scope)
    bonus = 5.0 if item.kind == "spec-section" and item.component in components else 0.0
    return overlap * 1.0 + bonus + KIND_WEIGHT.get(item.kind, 0.0) * 0.1


def rank(cands: list[CarriedItem], scope_text: str, components: list[str] | None = None) -> list[tuple[CarriedItem, float]]:
    scope, comps = tokens(scope_text), {c.lower() for c in (components or [])}
    scored = [(c, score(c, scope, comps)) for c in cands]
    return sorted(scored, key=lambda x: (-x[1], x[0].kind, x[0].id))


def select(
    cands_by_id: dict[str, CarriedItem], chosen: list[str], already: set[str], *,
    max_items: int = MAX_ITEMS, max_bytes: int = MAX_BYTES, used_items: int = 0, used_bytes: int = 0,
) -> tuple[list[CarriedItem], list[dict[str, str]]]:
    """Validate a selection against the candidates and the budget. Unknown ids, duplicates, already carried
    entries and anything over budget are REJECTED with a reason; the rest is accepted in the given order."""
    accepted: list[CarriedItem] = []
    rejected: list[dict[str, str]] = []
    seen: set[str] = set()
    for cid in chosen:
        item = cands_by_id.get(cid)
        if item is None:
            rejected.append({"id": cid, "reason": "not a candidate of the source release"})
        elif cid in seen:
            rejected.append({"id": cid, "reason": "listed twice"})
        elif cid in already:
            rejected.append({"id": cid, "reason": "already carried"})
        elif len(item.text.encode()) > MAX_TEXT:
            rejected.append({"id": cid, "reason": f"larger than {MAX_TEXT} characters"})
        elif used_items + len(accepted) + 1 > max_items:
            rejected.append({"id": cid, "reason": f"over the limit of {max_items} carried entries"})
        elif used_bytes + sum(len(a.text.encode()) for a in accepted) + len(item.text.encode()) > max_bytes:
            rejected.append({"id": cid, "reason": "over the carried-size budget"})
        else:
            seen.add(cid)
            accepted.append(item)
    return accepted, rejected


def derive_states(fork: ForkRecord, sections: dict[str, dict[str, str]]) -> ForkRecord:
    """Recompute each carried SPEC SECTION's state from the release's live spec files: unchanged → carried, edited →
    modified, gone → retired. `sections` is {component: {section: body}}. State is derived, never trusted."""
    out = fork.model_copy(deep=True)
    for c in out.carried:
        if c.kind != "spec-section" or not c.component or not c.carriedFrom:
            continue
        name = c.carriedFrom.ref
        body = sections.get(c.component, {}).get(name)
        if body is None:
            c.state, c.hash = "retired", ""
        else:
            c.hash = section_hash(body)
            c.state = "carried" if c.hash == c.carriedFrom.hash else "modified"
    return out


async def release_sections(ws: Any, m: Any, code: str, staged: dict[str, str]) -> dict[str, dict[str, str]]:
    """{component: {section: body}} of a release's spec files, with not-yet-written `staged` content on top."""
    out: dict[str, dict[str, str]] = {}
    for path, e in m.files.items():
        if e.kind == "spec" and e.release == code and path not in staged:
            out[path.rsplit("/", 1)[-1][:-3]] = parse_spec(await ws.read(path, manifest=m))[1]
    for path, md in staged.items():
        out[path.rsplit("/", 1)[-1][:-3]] = parse_spec(md)[1]
    return out


# ------------------------------------------------------------------ the model's part: ranking only
class LlmCarryPick(BaseModel):
    id: str
    reason: str = Field(default="", max_length=300)


class LlmCarry(BaseModel):
    picks: list[LlmCarryPick] = Field(default_factory=list)


_SYSTEM = (
    "You help start a new product release that is forked from an earlier one. You are given the NEW release's scope and "
    "a catalogue of context from the earlier release. Choose only the catalogue entries that the new scope will build on "
    "or must stay consistent with; prefer fewer, relevant entries. Reply with JSON "
    '{"picks": [{"id": <catalogue id exactly as given>, "reason": <one short sentence>}]}. Never invent ids.')


# ------------------------------------------------------------------ service
class CarryService:
    def __init__(self, db: Any, index: Any, audit: Any, llm: Any = None, settings: Any = None) -> None:
        self._db, self._index, self._audit, self._llm, self._settings = db, index, audit, llm, settings

    # ---- candidates (read the SOURCE release: its spec files, backlog, digests, retro notes)
    async def candidates(self, project_id: str, source: Any) -> list[CarriedItem]:
        ws = self._index.workspace(project_id)
        m = await ws.manifest()
        code = source["code"]
        out: list[CarriedItem] = []
        for path, e in m.files.items():
            if e.kind != "spec" or e.release != code:
                continue
            comp = path.rsplit("/", 1)[-1][:-3]
            _, sections = parse_spec(await ws.read(path, manifest=m))
            for name, body in sections.items():
                out.append(CarriedItem(
                    id=f"spec:{comp}/{name}", kind="spec-section", title=f"{comp} / {name}", text=body[:MAX_TEXT + 1],
                    component=comp, hash=section_hash(body),
                    carriedFrom=CarriedFrom(release=code, path=path, ref=name, hash=section_hash(body))))
        backlog = await self._db.list_backlog(project_id, release_id=source["id"], scope="release")
        for r in backlog:
            if r["status"] == "dropped" or not (r["type"] == "epic" or r["status"] == "done"):
                continue
            body = " ".join(filter(None, [r["description"] or "", "AC: " + "; ".join(r["acceptance_criteria"] or [])
                                          if r["acceptance_criteria"] else ""]))[:2000]
            out.append(CarriedItem(
                id=f"req:{r['item_key']}", kind="requirement", title=f"{r['item_key']} {r['title']}"[:200], text=body,
                carriedFrom=CarriedFrom(release=code, path="backlog", ref=r["item_key"], hash=section_hash(r["title"] + body))))
        rel_idx = await self._index.reader(project_id).release(code)
        for d in (rel_idx.decisions if rel_idx else []):
            out.append(CarriedItem(
                id=f"dec:{d.id}", kind="decision", title=d.title[:200], text=d.rationale,
                carriedFrom=CarriedFrom(release=code, path=f".devmind/releases/{code}/index.json", ref=d.id,
                                        hash=section_hash(d.title + d.rationale))))
        learning = await self._latest_retro(project_id, source)
        if learning:
            sprint, text = learning
            out.append(CarriedItem(
                id=f"learn:{sprint}", kind="learning", title=f"Retrospective {sprint}", text=text[:1500],
                carriedFrom=CarriedFrom(release=code, path="retro", ref=sprint, hash=section_hash(text[:1500]))))
        for c in out:                                        # the carried text IS the definition: hash what we copy
            if c.kind != "spec-section":
                c.hash = c.carriedFrom.hash if c.carriedFrom else ""
        return out

    async def _latest_retro(self, project_id: str, source: Any) -> tuple[str, str] | None:
        its = [i for i in await self._db.list_iterations(project_id) if i["release_id"] == source["id"] and i["status"] == "closed"]
        if not its:
            return None
        last = max(its, key=lambda i: i["number"])
        insts = {i["seq"] for i in await self._db.list_stage_instances(project_id) if i["iteration_id"] == last["id"]}
        arts = [a for a in await self._db.list_artefacts(project_id) if a["type"] == "RETRO_NOTES" and a["phase"] in insts]
        if not arts:
            return None
        a = arts[-1]
        return last["label"], (a["content"] or "").strip()

    # ---- suggestions: rules first, a model may re-rank
    async def suggest(self, project_id: str, source: Any, scope_text: str, *, components: list[str] | None = None,
                      limit: int = 15, use_ai: bool = True) -> dict[str, Any]:
        cands = await self.candidates(project_id, source)
        by_id = {c.id: c for c in cands}
        ranked = rank(cands, scope_text, components)
        relevant = [(c, s) for c, s in ranked if s >= 1.0]            # at least one real match
        picks: list[tuple[str, str]] = [(c.id, "matches the new scope") for c, _ in relevant[:limit]]
        origin = "rules"
        if use_ai and self._llm is not None and cands and scope_text.strip():
            try:
                ai = await self._ai_picks(scope_text, ranked[:80], by_id)
                if ai:
                    picks, origin = ai[:limit], "ai"
            except Exception as err:  # noqa: BLE001
                log.warning("carry suggestion model call failed: %s", err)
        accepted, rejected = select(by_id, [p for p, _ in picks], set())
        reasons = dict(picks)
        return {
            "source": origin,
            "suggestions": [{"id": c.id, "kind": c.kind, "title": c.title, "reason": reasons.get(c.id, ""),
                             "bytes": len(c.text.encode())} for c in accepted],
            "rejected": rejected,
            "candidates": [{"id": c.id, "kind": c.kind, "title": c.title, "bytes": len(c.text.encode()),
                            "tooLarge": len(c.text.encode()) > MAX_TEXT} for c in cands],
            "budget": {"maxItems": MAX_ITEMS, "maxBytes": MAX_BYTES},
        }

    async def _ai_picks(self, scope_text: str, ranked: list[tuple[CarriedItem, float]],
                        by_id: dict[str, CarriedItem]) -> list[tuple[str, str]]:
        catalogue = "\n".join(f"{c.id} | {c.kind} | {c.title} | {' '.join(c.text.split())[:160]}" for c, _ in ranked)
        user = f"## NEW RELEASE SCOPE\n{scope_text.strip()[:4000]}\n\n## CATALOGUE (id | kind | title | snippet)\n{catalogue}\n"
        out, _res = await self._llm.generate_json(
            intent="generation", tag="agile_carry", temperature=0.1, max_tokens=2048, schema=LlmCarry, max_attempts=2,
            messages=[{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}])
        return [(p.id, p.reason) for p in out.picks if p.id in by_id]       # only ids the catalogue really has

    # ---- applying (idempotent; additive; one writer)
    async def apply(self, project_id: str, release: Any, source: Any, chosen: list[str], *,
                    reasons: dict[str, str] | None = None, actor: str = "system") -> dict[str, Any]:
        """Copy the chosen candidates into `release`'s own files and record them in its fork.json. Entries already
        carried are skipped, so repeating the call (or extending the carry set later) is safe."""
        reasons = reasons or {}
        async with self._index._locked(project_id):
            ws = self._index.workspace(project_id)
            reader = self._index.reader(project_id)
            existing = await reader.fork(release["code"])
            cands = await self.candidates(project_id, source)
            by_id = {c.id: c for c in cands}
            have = {c.id for c in (existing.carried if existing else [])}
            used_bytes = sum(len(c.text.encode()) for c in (existing.carried if existing else []))
            accepted, rejected = select(by_id, chosen, have, used_items=len(have), used_bytes=used_bytes)
            if not accepted and not existing:
                accepted = []
            fork = existing or ForkRecord(release=release["code"], forkedFrom=source["code"],
                                          baseline=dict(release["fork_baseline"] or {}))
            m = await ws.manifest()
            specs: dict[str, str] = {}
            by_comp: dict[str, dict[str, CarriedItem]] = {}
            for c in accepted:
                c = c.model_copy(deep=True)
                c.reason = reasons.get(c.id, c.reason)[:300]
                c.state = "carried"
                fork.carried.append(c)
                if c.kind == "spec-section" and c.component:
                    by_comp.setdefault(c.component, {})[c.carriedFrom.ref if c.carriedFrom else c.title] = c
            for comp, secs in by_comp.items():
                path = release_spec_path(release["code"], comp)
                if path in m.files:
                    title, sections = parse_spec(await ws.read(path, manifest=m))
                else:
                    src_path = next(iter(secs.values())).carriedFrom.path          # type: ignore[union-attr]
                    title = parse_spec(await ws.read(src_path, manifest=m))[0] if src_path in m.files else comp
                    sections = {}
                for name, item in secs.items():
                    sections.setdefault(name, item.text)
                md = render_spec(comp, title or comp, sections)
                if len(md.encode()) > MAX_SPEC_BYTES:
                    raise SdlcError("VALIDATION_FAILED", f"The carried sections of '{comp}' would exceed the spec size limit")
                specs[path] = md
            fork = derive_states(fork, await release_sections(ws, m, release["code"], specs))
            extra = [StagedFile(fork_path(release["code"]), canonical_json(fork), "fork", release=release["code"]),
                     StagedFile(carried_path(release["code"]), carried_md(fork), "carried-md", release=release["code"])]
            await IndexBuilder(ws).update(specs=specs, extra=extra)
            await IndexBuilder(ws).update(release=await self._index.build_release(project_id, release, closed=False))
        self._audit.record(project_id=project_id, agent_role="Agile", event="release.carried", human_reviewer=actor,
                           detail={"release": release["code"], "from": source["code"],
                                   "carried": [c.id for c in accepted], "rejected": rejected[:20]})
        return {"carried": [c.id for c in accepted], "rejected": rejected, "total": len(fork.carried)}

    # ---- the live view of a release's carry set
    async def view(self, project_id: str, release: Any) -> dict[str, Any]:
        ws = self._index.workspace(project_id)
        m = await ws.manifest()
        fork = await self._index.reader(project_id).fork(release["code"])
        sections = await release_sections(ws, m, release["code"], {})
        derived = derive_states(fork, sections) if fork else None
        carried_keys = {(c.component, c.carriedFrom.ref) for c in (derived.carried if derived else [])
                        if c.kind == "spec-section" and c.carriedFrom}
        new = [{"id": f"spec:{comp}/{name}", "kind": "spec-section", "title": f"{comp} / {name}", "state": "new"}
               for comp, secs in sorted(sections.items()) for name in secs if (comp, name) not in carried_keys]
        src_changed: list[str] = []
        if derived and derived.forkedFrom:
            src = next((r for r in await self._db.list_releases(project_id) if r["code"] == derived.forkedFrom), None)
            if src is not None:
                now = {c.id: c for c in await self.candidates(project_id, src)}
                src_changed = [c.id for c in derived.carried if c.carriedFrom and c.id in now
                               and now[c.id].carriedFrom and now[c.id].carriedFrom.hash != c.carriedFrom.hash]
        counts: dict[str, int] = {}
        for c in (derived.carried if derived else []):
            counts[c.state] = counts.get(c.state, 0) + 1
        counts["new"] = len(new)
        return {
            "forkedFrom": derived.forkedFrom if derived else None, "baseline": derived.baseline if derived else {},
            "carried": [c.model_dump(mode="json", exclude={"text"}) | {"bytes": len(c.text.encode())}
                        for c in (derived.carried if derived else [])],
            "new": new, "counts": counts, "sourceChanged": src_changed,
            "budget": {"maxItems": MAX_ITEMS, "maxBytes": MAX_BYTES},
        }
