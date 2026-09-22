"""Dynamic, persona/domain-based expert steering.

Steering is NOT tied to phase numbers. Each file in the steering directory
declares the persona (with aliases) and domains it applies to; the pipeline
injects the matching steering by the *stage's persona* — so it works for the
built-in phases, reordered or added stages, and PM-defined custom stages alike.

Files: ``steering/<name>.md`` with YAML frontmatter::

    ---
    persona: Solution Architect
    aliases: [Solution Architect, SA, Cloud Architect]
    domains: [architecture, hld]
    fallback: false        # optional; the fallback pack matches when nothing else does
    ---
    <steering body>

Override the directory without a rebuild via ``STEERING_DIR``; files hot-reload
on mtime change (same spirit as the prompt/skill packs).
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.S)


def steering_dir() -> Path:
    env = os.environ.get("STEERING_DIR")
    for cand in (
        *( [Path(env)] if env else [] ),
        Path(__file__).resolve().parents[2] / "steering",
        Path("/app/steering"),
        Path.cwd() / "steering",
    ):
        if cand.is_dir():
            return cand
    return Path(__file__).resolve().parents[2] / "steering"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


_cache: dict[str, Any] = {}


def _load_packs() -> list[dict[str, Any]]:
    directory = steering_dir()
    try:
        files = sorted(directory.glob("*.md"))
    except OSError:
        return []
    sig = tuple((p.name, p.stat().st_mtime) for p in files)
    if _cache.get("sig") == sig:
        return _cache["packs"]
    packs: list[dict[str, Any]] = []
    for p in files:
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        m = _FRONTMATTER.match(text)
        if not m:
            continue
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            meta = {}
        aliases = meta.get("aliases") or [meta.get("persona", "")]
        packs.append({
            "persona": meta.get("persona", p.stem),
            "aliases": [a for a in aliases if a],
            "domains": [str(d) for d in (meta.get("domains") or [])],
            "fallback": bool(meta.get("fallback")) or ("*" in aliases),
            "body": m.group(2).strip(),
        })
    _cache.update(sig=sig, packs=packs)
    return packs


def resolve_steering(persona: str | None, domain: str | None = None) -> str:
    """Return the steering body for a persona (optionally biased by domain), or
    the fallback pack's body, or '' when there is no steering at all. Never raises."""
    packs = _load_packs()
    if not packs:
        return ""
    specific = [p for p in packs if not p["fallback"]]
    np = _norm(persona or "")

    # 1) exact alias / persona match.
    if np:
        for p in specific:
            if any(_norm(a) == np for a in p["aliases"]) or _norm(p["persona"]) == np:
                return p["body"]
        # 2) substring either way (e.g. "Lead Solution Architect" ~ "Solution Architect").
        for p in specific:
            if any(_norm(a) and (_norm(a) in np or np in _norm(a)) for a in p["aliases"]):
                return p["body"]

    # 3) domain hint.
    if domain:
        nd = _norm(domain)
        for p in specific:
            if any(_norm(d) == nd or nd in _norm(d) for d in p["domains"]):
                return p["body"]

    # 4) fallback pack.
    for p in packs:
        if p["fallback"]:
            return p["body"]
    return ""
