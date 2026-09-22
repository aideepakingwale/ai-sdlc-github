"""Configurable technology catalog for project creation.

The New Project form builds a *structured* stack — programming language →
version → framework(s) — from this catalog instead of a hardcoded list.

Ships a sensible default; override it WITHOUT a rebuild by pointing
``TECH_CATALOG_PATH`` at a JSON file of the same shape. The file is
hot-reloaded when its mtime changes (same live-edit spirit as the prompt/skill
packs), so an admin can add languages, versions or frameworks on the fly.

Shape::

    {"languages": [
       {"name": "Python", "versions": ["3.12", "3.11"],
        "frameworks": ["FastAPI", "Django"]},
       ...
    ]}
"""
from __future__ import annotations

import json
import os
from typing import Any

from ..config import get_settings

DEFAULT_TECH_CATALOG: dict[str, Any] = {
    "languages": [
        {"name": "Python", "versions": ["3.12", "3.11", "3.10"],
         "frameworks": ["FastAPI", "Django", "Flask"]},
        {"name": "TypeScript", "versions": ["5.x"],
         "frameworks": ["Node.js / Express", "NestJS", "React", "Next.js"]},
        {"name": "JavaScript", "versions": ["ES2023"],
         "frameworks": ["Node.js / Express", "React", "Vue"]},
        {"name": "Java", "versions": ["21 (LTS)", "17 (LTS)"],
         "frameworks": ["Spring Boot", "Quarkus", "Micronaut"]},
        {"name": "C#", "versions": [".NET 8", ".NET 6"],
         "frameworks": ["ASP.NET Core", "Minimal API"]},
        {"name": "Go", "versions": ["1.22", "1.21"],
         "frameworks": ["Gin", "Echo", "Fiber"]},
        {"name": "Rust", "versions": ["1.79"],
         "frameworks": ["Axum", "Actix Web"]},
    ],
}

_cache: dict[str, Any] = {}


def get_tech_catalog() -> dict[str, Any]:
    """Return the tech catalog — the default, or a hot-reloaded override file
    when ``TECH_CATALOG_PATH`` is set and valid. Never raises: an unreadable or
    malformed override falls back to the default so project creation never breaks."""
    path = get_settings().TECH_CATALOG_PATH
    if not path:
        return DEFAULT_TECH_CATALOG
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return DEFAULT_TECH_CATALOG
    if _cache.get("path") == path and _cache.get("mtime") == mtime:
        return _cache["data"]
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or not isinstance(data.get("languages"), list):
            raise ValueError("catalog must be an object with a 'languages' array")
        _cache.update(path=path, mtime=mtime, data=data)
        return data
    except (OSError, ValueError, json.JSONDecodeError):
        return DEFAULT_TECH_CATALOG


def compose_stack(
    language: str | None,
    version: str | None,
    frameworks: list[str] | None,
    fallback: str = "Node.js + TypeScript",
) -> str:
    """Compose the free-text ``tech_stack`` string the agents' prompts consume
    from the structured selection, e.g. ``"Python 3.12 + FastAPI, Pytest"``.
    Falls back to the legacy single-string value when no language is given."""
    language = (language or "").strip()
    if not language:
        return (fallback or "").strip() or "Node.js + TypeScript"
    version = (version or "").strip()
    fw = [f.strip() for f in (frameworks or []) if f and f.strip()]
    head = f"{language} {version}".strip()
    return f"{head} + {', '.join(fw)}" if fw else head
