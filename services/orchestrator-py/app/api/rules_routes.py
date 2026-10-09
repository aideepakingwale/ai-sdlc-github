"""Rules (Canon) and Templates (Formwork): the overview the Project Context screen reads, starter packs, organisation rules and stack presets,
drafting and checking rules, promoting a memory, what the agents see, and compliance evidence."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..agents.prompts import render_stack
from ..domain.errors import SdlcError
from ..domain.models import UserPublic, get_phase
from ..services import rule_packs
from ..services.formworks import suggest_mapping
from ..services.stack import is_stack_owner, stack_of, stack_source
from .deps import Container, current_user, get_container

router = APIRouter()


class TextBody(BaseModel):
    text: str


class OptOutBody(BaseModel):
    reason: str


class SuggestBody(BaseModel):
    name: str = ""
    template: str


class PresetBody(BaseModel):
    name: str
    description: str = ""
    layers: list[dict[str, Any]]


async def _can_author(c: Container, project_id: str, user: UserPublic) -> bool:
    try:
        await c.canon.assert_can_author(project_id, user)
        return True
    except SdlcError:
        return False


# ------------------------------------------------------------------ the Rules tab
@router.get("/api/projects/{project_id}/rules/overview")
async def rules_overview(project_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    """Everything the Rules tab shows: the project's rules, the organisation's (with opt-outs), starter packs and the latest compliance check."""
    entries = await c.canon.list(project_id, user, active_only=False)
    from ..services.rule_packs import KIND_LABEL, resolve_entries

    catalog = await c.canon.catalog.all()
    have = await c.canon.rule_titles(project_id)
    packs = []
    for p in catalog.values():
        entries = resolve_entries(catalog, p["id"])
        mine = sum(1 for e in entries if " ".join(e["title"].lower().split()) in have)
        packs.append({"id": p["id"], "name": p["name"], "description": p.get("description", ""), "kind": p["kind"], "kindLabel": KIND_LABEL[p["kind"]],
                      "tags": p["tags"], "includes": [{"id": i, "name": catalog[i]["name"]} for i in p["includes"] if i in catalog], "version": p["version"],
                      "baseline": p["baseline"], "count": len(entries), "alreadyHave": mine, "source": p.get("source", "builtin"),
                      "stages": sorted({e["stage"] for e in entries if e.get("stage")})})
    packs.sort(key=lambda x: (x["kind"] != "bundle", x["kind"], x["name"]))
    compliance = await c.rule_checker.summary(project_id) if getattr(c, "rule_checker", None) else {"rules": {}, "artefacts": {}}
    return {"entries": entries, "orgEntries": await c.canon.inherited(project_id, user), "canAuthor": await _can_author(c, project_id, user),
            "packs": packs, "compliance": compliance}


@router.post("/api/projects/{project_id}/rules/packs/{pack_id}")
async def apply_pack(project_id: str, pack_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return await c.canon.apply_pack(project_id, user, pack_id)


class IdsBody(BaseModel):
    ids: list[str]


class StageBody(BaseModel):
    stage: int


class ProfileEdit(BaseModel):
    upsert: list[dict[str, Any]] = []
    remove: list[str] = []
    confirm: list[str] = []


class OrgProfileBody(BaseModel):
    items: list[dict[str, Any]]


class ImportBody(BaseModel):
    text: str


@router.post("/api/projects/{project_id}/rules/packs-apply")
async def apply_packs(project_id: str, body: IdsBody, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    """Apply several packs at once (a recommendation's "Apply all")."""
    return await c.canon.apply_packs(project_id, user, body.ids)


@router.get("/api/projects/{project_id}/rules/recommendations")
async def rule_recommendations(project_id: str, stage: int | None = None, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    """Which packs fit this project's profile and what it does not have yet. `due` is true on stages 2 and 3 when there is something to advise."""
    return await c.rule_advisor.recommend(project_id, user, stage)


@router.post("/api/projects/{project_id}/rules/recommendations/dismiss")
async def dismiss_recommendations(project_id: str, body: StageBody, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    await c.rule_advisor.dismiss(project_id, user, body.stage)
    return {"ok": True}


@router.get("/api/projects/{project_id}/profile")
async def get_profile(project_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    """The project's profile: its own values, the organisation's default and what applies."""
    from ..services import profile as prof

    await c.authz.assert_project_access(project_id, user)
    p = await c.project_config.profile(project_id)
    return {**p, "canEdit": await c.project_config.can_edit(project_id, user), "vocab": prof.VOCAB, "kinds": {k: {"label": prof.KIND_LABEL[k], "single": k in prof.SINGLE} for k in prof.KINDS},
            "text": prof.describe(p["effective"])}


@router.put("/api/projects/{project_id}/profile")
async def update_profile(project_id: str, body: ProfileEdit, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    await c.project_config.update_profile(project_id, user, upsert=body.upsert, remove=body.remove, confirm=body.confirm)
    return await get_profile(project_id, user, c)


@router.post("/api/projects/{project_id}/rules/draft")
async def draft_rules(project_id: str, body: TextBody, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    """Propose rules from a standards document. Nothing is saved."""
    await c.authz.assert_project_access(project_id, user)
    return await c.rule_assist.draft(project_id, user, body.text)


@router.post("/api/projects/{project_id}/rules/check")
async def check_rule(project_id: str, body: dict, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    """Duplicates, contradictions and clashes with the pinned stack, for a rule about to be saved. Advice only."""
    await c.authz.assert_project_access(project_id, user)
    return await c.rule_assist.check(project_id, user, body)


@router.post("/api/projects/{project_id}/rules/from-memory/{memory_id}")
async def rule_from_memory(project_id: str, memory_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return {"entry": await c.canon.from_memory(project_id, memory_id, user)}


@router.put("/api/projects/{project_id}/rules/org/{entry_id}/opt-out")
async def opt_out(project_id: str, entry_id: str, body: OptOutBody, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    await c.canon.opt_out(project_id, entry_id, user, body.reason)
    return {"ok": True}


@router.delete("/api/projects/{project_id}/rules/org/{entry_id}/opt-out")
async def opt_in(project_id: str, entry_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    await c.canon.opt_in(project_id, entry_id, user)
    return {"ok": True}


@router.get("/api/projects/{project_id}/artefacts/{artefact_id}/rules")
async def artefact_rules(project_id: str, artefact_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    """How the latest check found this artefact against the project's must-rules."""
    await c.authz.assert_project_access(project_id, user)
    summary = await c.rule_checker.summary(project_id) if getattr(c, "rule_checker", None) else {"artefacts": {}}
    return summary["artefacts"].get(artefact_id) or {"complied": 0, "violated": 0, "unclear": 0, "items": []}


# ------------------------------------------------------------------ what the agents see
@router.get("/api/projects/{project_id}/context-preview")
async def context_preview(project_id: str, stage: int = 3, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    """The exact stack, rules and template text one stage's agents receive, with a rough token count for each."""
    await c.authz.assert_project_access(project_id, user)
    if not 1 <= stage <= 6:
        raise SdlcError("VALIDATION_FAILED", "stage must be 1 to 6")
    project = await c.db.get_project(project_id) or {}
    layers = await c.project_config.layers(project_id)
    stack_block = render_stack(stack_of(project), owner=is_stack_owner(template=stage), source=stack_source(project), layers=layers, stage=stage)
    canon_block = await c.canon.render_block(project_id, stage)
    formwork_block = await c.formworks.render_block(project_id, list(get_phase(stage).produces))
    tok = lambda s: max(0, len(s) // 4)  # noqa: E731 - a rough estimate, labelled as one on the screen
    return {"stage": stage, "name": get_phase(stage).name, "stack": stack_block, "rules": canon_block, "templates": formwork_block,
            "tokens": {"stack": tok(stack_block), "rules": tok(canon_block), "templates": tok(formwork_block),
                       "total": tok(stack_block) + tok(canon_block) + tok(formwork_block)}}


# ------------------------------------------------------------------ templates
@router.post("/api/projects/{project_id}/formworks/suggest")
async def suggest_formwork(project_id: str, body: SuggestBody, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    """What a dropped file probably is (artefact type and format) and what is in it. The person confirms."""
    await c.authz.assert_project_access(project_id, user)
    return suggest_mapping(body.name, body.template)


@router.post("/api/formworks/suggest")
async def suggest_platform_formwork(body: SuggestBody, user: UserPublic = Depends(current_user)) -> dict:
    """The same for the platform-wide library (administrators)."""
    if user.role != "SUPER_ADMIN":
        raise SdlcError("FORBIDDEN", "Only an administrator can publish platform-wide templates")
    return suggest_mapping(body.name, body.template)


# ------------------------------------------------------------------ organisation (administrators)
@router.get("/api/org/rules")
async def org_rules(user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return {"entries": await c.canon.org_list(user), "canEdit": user.role == "SUPER_ADMIN"}


@router.post("/api/org/rules")
async def org_rule_create(body: dict, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return {"entry": await c.canon.org_create(user, body)}


@router.patch("/api/org/rules/{entry_id}")
async def org_rule_update(entry_id: str, body: dict, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return {"entry": await c.canon.org_update(entry_id, user, body)}


@router.delete("/api/org/rules/{entry_id}")
async def org_rule_delete(entry_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    await c.canon.org_delete(entry_id, user)
    return {"deleted": True}


@router.get("/api/org/profile")
async def org_profile(user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    from ..services import profile as prof

    items = await c.project_config.org_profile()
    return {"items": items, "canEdit": user.role == "SUPER_ADMIN", "vocab": prof.VOCAB, "kinds": {k: {"label": prof.KIND_LABEL[k], "single": k in prof.SINGLE} for k in prof.KINDS}}


@router.put("/api/org/profile")
async def set_org_profile(body: OrgProfileBody, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return {"items": await c.project_config.set_org_profile(user, body.items)}


@router.get("/api/org/packs")
async def org_packs(user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    """Every pack (built-in samples and the organisation's own) with where it comes from."""
    from ..services import profile as prof
    from ..services.rule_packs import KIND_LABEL

    return {"packs": await c.pack_admin.list(), "canEdit": user.role == "SUPER_ADMIN", "kinds": KIND_LABEL,
            "vocab": {k: prof.VOCAB[k] for k in ("industry", "regulation", "domain")}}


@router.get("/api/org/packs/{pack_id}")
async def org_pack(pack_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return {"pack": await c.pack_admin.get(pack_id)}


@router.put("/api/org/packs")
async def org_pack_save(body: dict, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return {"pack": await c.pack_admin.save(user, body)}


@router.post("/api/org/packs/import")
async def org_pack_import(body: ImportBody, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return {"pack": await c.pack_admin.import_text(user, body.text)}


@router.get("/api/org/packs/{pack_id}/export")
async def org_pack_export(pack_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)):
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(await c.pack_admin.export_text(pack_id), media_type="text/yaml")


@router.post("/api/org/packs/{pack_id}/hide")
async def org_pack_hide(pack_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    await c.pack_admin.hide(user, pack_id)
    return {"ok": True}


@router.delete("/api/org/packs/{pack_id}")
async def org_pack_delete(pack_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return {"result": await c.pack_admin.delete_or_restore(user, pack_id)}


@router.get("/api/org/stack-presets")
async def org_presets(user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return {"presets": await c.project_config.presets(), "canEdit": user.role == "SUPER_ADMIN"}


@router.post("/api/org/stack-presets")
async def org_preset_create(body: PresetBody, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    return {"preset": await c.project_config.create_preset(user, body.name, body.description, body.layers)}


@router.delete("/api/org/stack-presets/{preset_id}")
async def org_preset_delete(preset_id: str, user: UserPublic = Depends(current_user), c: Container = Depends(get_container)) -> dict:
    await c.project_config.delete_preset(user, preset_id)
    return {"deleted": True}
