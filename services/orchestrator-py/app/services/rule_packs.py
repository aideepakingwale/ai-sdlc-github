"""Rule packs and stack presets.

A **pack** is a named set of rules for one practice (secure engineering), one regulation (PCI DSS), one industry (banking) or a ready
combination of those (a *bundle*, which just lists the packs it includes). Packs carry tags (industries, regulations, domains) so the
platform can recommend the ones that fit a project's profile.

Where packs come from:
  * built-in samples: YAML files under `packs/rules` that ship with the platform, read-only;
  * the organisation's own: rows in `org_packs`, created, imported or edited by administrators. A row with the id of a built-in pack
    replaces it for the whole organisation ("customised"); an inactive row hides it. Deleting the row restores the built-in.

Stack presets (`packs/stack`) are read the same way; organisation presets live in `org_stack_presets`.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ..domain.errors import SdlcError
from . import profile as prof
from .tech_catalog import layer_ids

CATEGORIES = ("rule", "decision", "glossary", "constraint", "preference")
PRIORITIES = ("must", "should", "context")
KINDS = ("practice", "regulation", "industry", "bundle")
KIND_LABEL = {"practice": "Practice", "regulation": "Regulation", "industry": "Industry", "bundle": "Ready-made set"}
TAG_KEYS = {"industries": "industry", "regulations": "regulation", "domains": "domain"}
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
MAX_DEPTH = 4


class PackError(RuntimeError):
    pass


def default_packs_dir() -> Path:
    env = os.environ.get("PACKS_DIR")
    for c in [*([Path(env)] if env else []), Path(__file__).resolve().parents[2] / "packs", Path("/app/packs"), Path.cwd() / "packs"]:
        if c.is_dir():
            return c
    return Path(__file__).resolve().parents[2] / "packs"


# ---------------------------------------------------------------- validation (files and organisation packs share it)
def normalise_pack(data: dict[str, Any], *, known_ids: set[str] | None = None) -> dict[str, Any]:
    """A validated, normalised pack. Raises ValueError with a readable message; `known_ids` (when given) checks that includes exist."""
    pid = str(data.get("id") or "").strip()
    if not _ID.match(pid):
        raise ValueError("The id is lower-case letters, numbers and hyphens, 3 to 64 characters")
    name = str(data.get("name") or "").strip()
    if len(name) < 3:
        raise ValueError(f"{pid}: a pack needs a name")
    kind = str(data.get("kind") or "practice")
    if kind not in KINDS:
        raise ValueError(f"{pid}: kind must be one of {list(KINDS)}")
    tags_in = data.get("tags") or {}
    tags: dict[str, list[str]] = {}
    for key, vocab_kind in TAG_KEYS.items():
        vals = [str(v).strip().lower() for v in (tags_in.get(key) or [])]
        bad = [v for v in vals if v not in prof.valid_ids(vocab_kind)]
        if bad:
            raise ValueError(f"{pid}: unknown {key} {bad}; known: {sorted(prof.valid_ids(vocab_kind))}")
        tags[key] = vals
    includes = [str(i).strip() for i in (data.get("includes") or [])]
    entries = []
    for e in data.get("entries") or []:
        if e.get("category") not in CATEGORIES or e.get("priority") not in PRIORITIES:
            raise ValueError(f"{pid}: bad category or priority in '{e.get('title')}'")
        stage = e.get("stage")
        if stage is not None and not (isinstance(stage, int) and 1 <= stage <= 6):
            raise ValueError(f"{pid}: stage must be 1-6 or empty in '{e.get('title')}'")
        title, body = str(e.get("title") or "").strip(), str(e.get("body") or "").strip()
        if len(title) < 3 or not body:
            raise ValueError(f"{pid}: every rule needs a title and a body")
        entries.append({"category": e["category"], "priority": e["priority"], "stage": stage, "title": title[:120], "body": body[:1500]})
    if kind == "bundle":
        if not includes:
            raise ValueError(f"{pid}: a ready-made set must include at least one pack")
        if entries:
            raise ValueError(f"{pid}: a ready-made set lists packs, not rules of its own")
        if pid in includes:
            raise ValueError(f"{pid}: a set cannot include itself")
        if known_ids is not None and (missing := [i for i in includes if i not in known_ids]):
            raise ValueError(f"{pid}: includes unknown packs {missing}")
    else:
        if includes:
            raise ValueError(f"{pid}: only a ready-made set can include other packs")
        if not entries:
            raise ValueError(f"{pid}: a pack needs at least one rule")
    return {"id": pid, "name": name, "description": str(data.get("description") or "").strip()[:500], "kind": kind,
            "version": max(1, int(data.get("version") or 1)), "baseline": bool(data.get("baseline", False)), "tags": tags,
            "includes": includes, "entries": entries}


def _load_files(kind_dir: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in sorted((default_packs_dir() / kind_dir).glob("*.yaml")):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as err:
            raise PackError(f"{f.name}: {err}") from err
        if str(data.get("id") or "") != f.stem:
            raise PackError(f"{f.name}: id must be '{f.stem}'")
        if kind_dir == "rules":
            try:
                out.append({**normalise_pack(data), "source": "builtin"})
            except ValueError as err:
                raise PackError(f"{f.name}: {err}") from err
        else:
            layers = data.get("layers") or []
            if not data.get("name") or not layers:
                raise PackError(f"{f.name}: a stack preset needs a name and layers")
            for layer in layers:
                if layer.get("layer") not in layer_ids() or not layer.get("technology"):
                    raise PackError(f"{f.name}: bad layer {layer}")
            out.append(data)
    return out


@lru_cache(maxsize=2)
def _cached(kind_dir: str) -> tuple[dict[str, Any], ...]:
    packs = _load_files(kind_dir)
    if kind_dir == "rules":
        ids = {p["id"] for p in packs}
        for p in packs:
            if p["kind"] == "bundle" and (missing := [i for i in p["includes"] if i not in ids]):
                raise PackError(f"{p['id']}: includes unknown packs {missing}")
        for p in packs:
            resolve_entries({x["id"]: x for x in packs}, p["id"])      # raises on a cycle
    return tuple(packs)


def builtin_packs() -> list[dict[str, Any]]:
    return [dict(p) for p in _cached("rules")]


def rule_packs() -> list[dict[str, Any]]:               # kept for older callers and tests
    return builtin_packs()


def stack_presets() -> list[dict[str, Any]]:
    return list(_cached("stack"))


def get_stack_preset(preset_id: str) -> dict[str, Any] | None:
    return next((x for x in stack_presets() if x["id"] == preset_id), None)


# ---------------------------------------------------------------- resolving a pack (a bundle expands to its parts)
def _key(title: str) -> str:
    return " ".join(title.lower().split())


def resolve_entries(catalog: dict[str, dict[str, Any]], pack_id: str, _depth: int = 0, _seen: tuple[str, ...] = (), *, _top: bool = True) -> list[dict[str, Any]]:
    """The rules a pack stands for, each tagged with the pack that holds it (`pack`). A bundle expands to its included packs, in order,
    with a rule that appears in more than one kept once."""
    if pack_id in _seen:
        raise PackError(f"{pack_id}: packs include each other in a loop ({' > '.join((*_seen, pack_id))})")
    if _depth > MAX_DEPTH:
        raise PackError(f"{pack_id}: packs are nested too deeply")
    p = catalog.get(pack_id)
    if p is None:
        raise SdlcError("NOT_FOUND", f"Pack '{pack_id}' not found")
    if p["kind"] != "bundle":
        return [{**e, "pack": p["id"], "packVersion": p["version"]} for e in p["entries"]]
    out: list[dict[str, Any]] = []
    have: set[str] = set()
    for child in p["includes"]:
        if child not in catalog:
            continue                    # an included pack the organisation has hidden or removed: the rest still applies
        for e in resolve_entries(catalog, child, _depth + 1, (*_seen, pack_id), _top=False):
            if _key(e["title"]) not in have:
                have.add(_key(e["title"]))
                out.append(e)
    return out


def leaf_ids(catalog: dict[str, dict[str, Any]], pack_id: str) -> list[str]:
    seen: list[str] = []
    for e in resolve_entries(catalog, pack_id):
        if e["pack"] not in seen:
            seen.append(e["pack"])
    return seen


# ---------------------------------------------------------------- recommending
def match(pack: dict[str, Any], values: dict[str, list[str]]) -> tuple[int, list[str]]:
    """How well a pack fits a profile (score) and why, in words. Industry counts most, then regulation, then what the system does."""
    score, why = 0, []
    for key, vocab_kind, weight in (("industries", "industry", 3), ("regulations", "regulation", 2), ("domains", "domain", 1)):
        hit = [v for v in pack["tags"].get(key, []) if v in values.get(vocab_kind, [])]
        score += weight * len(hit)
        why += [prof.label_of(vocab_kind, v) for v in hit]
    return score, why


def recommend(catalog: dict[str, dict[str, Any]], values: dict[str, list[str]], have_titles: set[str]) -> dict[str, Any]:
    """Which packs to suggest for a profile, and how much of each the project already has.

    Returns {"bundles": [...], "packs": [...], "baseline": [...]}, best first. A pack that a recommended bundle already covers is not
    repeated. Each entry has the pack's id, name, kind, reasons, rule counts and `missing` (rules the project does not have yet)."""
    def view(p: dict[str, Any], score: int, why: list[str]) -> dict[str, Any]:
        entries = resolve_entries(catalog, p["id"])
        have = sum(1 for e in entries if _key(e["title"]) in have_titles)
        return {"id": p["id"], "name": p["name"], "kind": p["kind"], "description": p["description"], "version": p["version"], "score": score,
                "reasons": why, "total": len(entries), "have": have, "missing": len(entries) - have,
                "includes": [{"id": c, "name": catalog[c]["name"]} for c in p["includes"] if c in catalog]}

    has_profile = any(values.get(k) for k in values)
    scored = [(p, *match(p, values)) for p in catalog.values()]
    bundles = sorted([view(p, s, w) for p, s, w in scored if p["kind"] == "bundle" and s >= 3], key=lambda v: (-v["score"], v["name"]))
    covered = {c for b in bundles[:1] for c in leaf_ids(catalog, b["id"])}
    packs = sorted([view(p, s, w) for p, s, w in scored if p["kind"] != "bundle" and s > 0 and p["id"] not in covered and not p["baseline"]],
                   key=lambda v: (-v["score"], v["name"]))
    base_bundles = [p for p in catalog.values() if p["baseline"] and p["kind"] == "bundle"]
    in_base = {c for b in base_bundles for c in leaf_ids(catalog, b["id"])}
    baseline = [view(p, 0, ["Every project"]) for p in catalog.values()
                if p["baseline"] and p["id"] not in covered and (p["kind"] == "bundle" or p["id"] not in in_base)]
    return {"bundles": bundles[:3], "packs": packs[:8], "baseline": baseline, "hasProfile": has_profile}


# ---------------------------------------------------------------- the catalog as the organisation sees it
class PackCatalog:
    """Built-in samples plus the organisation's own packs."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def all(self, *, include_hidden: bool = False) -> dict[str, dict[str, Any]]:
        cat: dict[str, dict[str, Any]] = {p["id"]: p for p in builtin_packs()}
        rows = await self._db.list_org_packs() if hasattr(self._db, "list_org_packs") else []
        for r in rows:
            p = {"id": r["id"], "name": r["name"], "description": r["description"], "kind": r["kind"], "version": int(r["version"]),
                 "baseline": bool(r["baseline"]), "tags": r["tags"], "includes": r["includes"], "entries": r["entries"],
                 "source": "customised" if r["id"] in cat else "org", "active": bool(r["active"])}
            if not r["active"] and not include_hidden:
                cat.pop(r["id"], None)
                continue
            cat[r["id"]] = p
        return cat

    async def get(self, pack_id: str) -> dict[str, Any]:
        cat = await self.all()
        if pack_id not in cat:
            raise SdlcError("NOT_FOUND", "Pack not found")
        return cat[pack_id]

    async def entries(self, pack_id: str) -> list[dict[str, Any]]:
        return resolve_entries(await self.all(), pack_id)
