"""Project config (projectconfig.json): the stack by layer, read by anyone on the project and edited by the people who author its rules."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from ..domain.errors import SdlcError
from ..domain.models import UserPublic
from ..services.project_config import render_entry
from ..services.tech_catalog import get_layers, get_tech_catalog
from .deps import Container, current_user, get_container

router = APIRouter()


class StackEntryIn(BaseModel):
    id: str | None = None
    layer: str
    technology: str = ""
    version: str = ""
    extras: list[str] = Field(default_factory=list)
    component: str = ""
    notes: str = ""
    status: str = "pinned"          # pinned | open


class StackUpdate(BaseModel):
    upsert: list[StackEntryIn] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)
    confirm: list[str] = Field(default_factory=list)


def _catalog() -> dict[str, Any]:
    cat = get_tech_catalog()
    langs = cat.get("languages", [])
    layers = []
    for layer in get_layers():
        options = list(layer.get("options") or [])
        if layer["id"] == "backend":
            options = [str(x.get("name")) for x in langs if x.get("name")]
        layers.append({"id": layer["id"], "label": layer.get("label", layer["id"]), "hint": layer.get("hint", ""), "options": options})
    return {"layers": layers, "languages": langs}


@router.get("/api/projects/{project_id}/config")
async def get_config(project_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    await c.authz.assert_project_access(project_id, user)
    cfg = await c.project_config.get(project_id)
    presets = await c.project_config.presets()
    return {"config": cfg, "canEdit": await c.project_config.can_edit(project_id, user), "catalog": _catalog(), "presets": presets,
            "summary": cfg["stack"]["summary"], "rendered": {e["id"]: render_entry(e) for e in cfg["stack"]["layers"]}}


@router.put("/api/projects/{project_id}/config/stack")
async def update_stack(project_id: str, body: StackUpdate, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    """Pin, edit, leave open or remove stack entries; `confirm` pins identified entries as they are."""
    cfg = None
    if body.confirm:
        cfg = await c.project_config.confirm(project_id, user, body.confirm)
    if body.upsert or body.remove:
        cfg = await c.project_config.update(project_id, user, upsert=[e.model_dump() for e in body.upsert], remove=body.remove)
    if cfg is None:
        raise SdlcError("VALIDATION_FAILED", "Nothing to change")
    return {"config": cfg, "summary": cfg["stack"]["summary"]}


@router.post("/api/projects/{project_id}/config/stack/advise")
async def advise_stack(project_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    """Re-run the stack advisor over the project's finished stages and its uploaded codebase."""
    await c.project_config.assert_can_edit(project_id, user)
    changes = await c.stack_advisor.recheck(project_id, c.content)
    return {"changes": changes, "config": await c.project_config.get(project_id)}


@router.get("/api/projects/{project_id}/config/file")
async def config_file(project_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> PlainTextResponse:
    """projectconfig.json exactly as it is stored."""
    await c.authz.assert_project_access(project_id, user)
    import json
    cfg = await c.project_config.get(project_id)
    return PlainTextResponse(json.dumps(cfg, indent=2, ensure_ascii=False), media_type="application/json")


@router.post("/api/projects/{project_id}/config/stack/preset/{preset_id}")
async def apply_preset(project_id: str, preset_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    """Pin an organisation (or starter) preset's layers on this project."""
    cfg = await c.project_config.apply_preset(project_id, user, preset_id)
    return {"config": cfg, "summary": cfg["stack"]["summary"]}
