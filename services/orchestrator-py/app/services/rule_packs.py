"""Starter packs: ready-made rule sets and stack presets that ship with the platform (`packs/rules`, `packs/stack`).

A pack is a small YAML file. Rule packs add rules to a project in one click; stack presets pin a set of layers. They are read-only here;
organisation administrators add their own rules and presets in the database (`org_canon`, `org_stack_presets`).
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ..domain.errors import SdlcError
from .tech_catalog import layer_ids

CATEGORIES = ("rule", "decision", "glossary", "constraint", "preference")
PRIORITIES = ("must", "should", "context")


class PackError(RuntimeError):
    pass


def default_packs_dir() -> Path:
    env = os.environ.get("PACKS_DIR")
    for c in [*([Path(env)] if env else []), Path(__file__).resolve().parents[2] / "packs", Path("/app/packs"), Path.cwd() / "packs"]:
        if c.is_dir():
            return c
    return Path(__file__).resolve().parents[2] / "packs"


def _load(kind: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in sorted((default_packs_dir() / kind).glob("*.yaml")):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as err:
            raise PackError(f"{f.name}: {err}") from err
        if str(data.get("id") or "") != f.stem:
            raise PackError(f"{f.name}: id must be '{f.stem}'")
        if not data.get("name"):
            raise PackError(f"{f.name}: a pack needs a name")
        if kind == "rules":
            entries = data.get("entries") or []
            if not entries:
                raise PackError(f"{f.name}: a rule pack needs entries")
            for e in entries:
                if e.get("category") not in CATEGORIES or e.get("priority") not in PRIORITIES:
                    raise PackError(f"{f.name}: bad category or priority in '{e.get('title')}'")
                if e.get("stage") is not None and not (isinstance(e["stage"], int) and 1 <= e["stage"] <= 6):
                    raise PackError(f"{f.name}: stage must be 1-6 or null in '{e.get('title')}'")
                if len(str(e.get("title") or "")) < 3 or not str(e.get("body") or "").strip():
                    raise PackError(f"{f.name}: every entry needs a title and a body")
        else:
            layers = data.get("layers") or []
            if not layers:
                raise PackError(f"{f.name}: a stack preset needs layers")
            for layer in layers:
                if layer.get("layer") not in layer_ids() or not layer.get("technology"):
                    raise PackError(f"{f.name}: bad layer {layer}")
        out.append(data)
    return out


@lru_cache(maxsize=2)
def _cached(kind: str) -> tuple[dict[str, Any], ...]:
    return tuple(_load(kind))


def rule_packs() -> list[dict[str, Any]]:
    return list(_cached("rules"))


def stack_presets() -> list[dict[str, Any]]:
    return list(_cached("stack"))


def get_rule_pack(pack_id: str) -> dict[str, Any]:
    p = next((x for x in rule_packs() if x["id"] == pack_id), None)
    if p is None:
        raise SdlcError("NOT_FOUND", "Starter pack not found")
    return p


def get_stack_preset(preset_id: str) -> dict[str, Any] | None:
    return next((x for x in stack_presets() if x["id"] == preset_id), None)
