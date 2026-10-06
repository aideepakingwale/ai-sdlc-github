"""The context manifest: what a stage knows when it runs, as structured data.

Layers (fixed instructions, project profile, project canon & templates, previous-stage artifacts, the
reviewer's input, attached material, retrieved knowledge) each hold items with a status
(full / condensed / summarised / excluded) and a size. The same builder serves both views:

  preview  built from the plan screen's assembled inputs ("what will be sent")
  actual   built at trigger time from the exact inputs of the run, and stored with it ("what was sent")

`build_manifest` is pure: ChatService hands it the very objects it assembled the prompt from (the
context window, retrieved snippets, the attachment/reference records `_resolve_extra_context` kept),
so the manifest cannot describe something different from the prompt. Sizes are character counts with a
chars/4 token estimate - indicative, not billing-accurate.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .prompt_library import render as render_prompt

CHARS_PER_TOKEN = 4
PRIOR_ARTIFACT_CHARS = 1_200          # what the prompt keeps of each previous-stage artifact (see _assemble_prompt_preview)

LAYERS: list[tuple[str, str, str]] = [
    ("instructions", "Fixed instructions", "Platform policy, engineering standard and the stage's quality bar. Not editable."),
    ("project", "Project profile", "Technology stack, project traits and whether a codebase is attached."),
    ("canon", "Standards & templates", "Organisation canon and the output templates the artifacts must follow."),
    ("upstream", "Previous stages", "Artifacts approved in earlier stages that this stage builds on."),
    ("input", "Your instructions", "What the reviewer asked for in the plan."),
    ("revision", "Amendment & history", "The previous version being amended, the amendments requested so far and the clarifications already answered."),
    ("attached", "Attached material", "Files and artifacts the reviewer attached or @-referenced for this stage."),
    ("retrieved", "Retrieved knowledge", "Enterprise standards and approved artifacts found by similarity search."),
]


def tokens_of(chars: int) -> int:
    return (max(0, chars) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def _item(layer: str, key: str, label: str, *, chars: int, total: int | None = None, status: str = "full",
          kind: str = "item", source: dict[str, Any] | None = None, note: str = "") -> dict[str, Any]:
    total = chars if total is None else total
    return {"id": f"{layer}:{key}", "layer": layer, "label": label, "kind": kind, "status": status,
            "chars": chars, "totalChars": total, "tokens": tokens_of(chars), "source": source or {}, "note": note}


def _safe_prompt_chars(prompt_id: str) -> int:
    try:
        return len(render_prompt(prompt_id))
    except Exception:  # noqa: BLE001 - templates with required variables: size is not worth failing for
        return 0


def _instruction_items(template: int, stage_name: str, persona: str) -> list[dict[str, Any]]:
    out = [
        _item("instructions", "policy", "Responsible-AI policy", chars=_safe_prompt_chars("policy.responsible_ai"),
              source={"type": "prompt", "id": "policy.responsible_ai"}),
        _item("instructions", "craft", "Engineering craft standard", chars=_safe_prompt_chars("phase.system.craft"),
              source={"type": "prompt", "id": "phase.system.craft"}),
    ]
    if 1 <= template <= 6:
        out.append(_item("instructions", "quality", f"Quality bar - {stage_name}", chars=_safe_prompt_chars(f"phase.quality.{template}"),
                         source={"type": "prompt", "id": f"phase.quality.{template}"}))
    out.append(_item("instructions", "persona", f"Persona - {persona or 'Specialist'}", chars=len(persona or "") + 80,
                     source={"type": "stage", "name": stage_name}))
    return out


def build_manifest(
    *, mode: str, phase: int, stage: dict[str, Any], project: dict[str, Any], overlay: dict[str, Any],
    context_artifacts: list[Any], snippets: list[dict[str, Any]], canon_block: str,
    formworks: list[dict[str, Any]], attached: list[dict[str, Any]], traits: dict[str, bool] | None,
    has_codebase: bool, codebase_files: int = 0, formats: dict[str, dict[str, str]] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Assemble the manifest. `attached` is the per-item record `_resolve_extra_context` kept
    ({kind: attachment|reference|template, id, label, chars, totalChars, status, note, ...})."""
    formats = formats or {}
    items: list[dict[str, Any]] = []
    persona = stage.get("persona") or ""
    items += _instruction_items(int(stage.get("template") or 0), stage.get("name") or "", persona)

    stack = str(project.get("tech_stack") or "").strip()
    items.append(_item("project", "stack", f"Technology stack: {stack}" if stack else "Technology stack: undecided",
                       chars=len(stack) + 40, status="full" if stack else "excluded",
                       source={"type": "project", "field": "tech_stack", "decidedBy": project.get("tech_stack_source") or ""},
                       note="" if stack else "Decided by the Technical Architect stage"))
    profile = str(project.get("description") or project.get("name") or "")
    items.append(_item("project", "profile", "Project profile", chars=len(profile),
                       source={"type": "project", "field": "profile"}))
    on = sorted(k for k, v in (traits or {}).items() if v)
    items.append(_item("project", "traits", f"Project traits ({len(on)} apply)", chars=sum(len(t) + 20 for t in on),
                       source={"type": "project", "field": "traits"}, note=", ".join(on[:12])))
    if has_codebase:
        items.append(_item("project", "codebase", f"Existing codebase ({codebase_files} files)", chars=0,
                           source={"type": "codebase"}, note="Referenced for brownfield work; files are searched, not inlined"))

    if canon_block:
        items.append(_item("canon", "canon", "Organisation canon", chars=len(canon_block), source={"type": "canon"}))
    for f in formworks:
        items.append(_item("canon", f"formwork:{f['id']}", f"Template - {f['name']} ({f['artefactType']})",
                           chars=int(f.get("chars") or 0), status=f.get("status", "full"), kind="template",
                           source={"type": "formwork", "id": f["id"], "scope": f.get("scope", ""), "artefactType": f["artefactType"]},
                           note=f.get("note", "")))

    for i, a in enumerate(context_artifacts):
        body = a.content or a.summary or ""
        kept = min(len(body), PRIOR_ARTIFACT_CHARS)
        status = "summarised" if not a.content else ("condensed" if len(body) > PRIOR_ARTIFACT_CHARS else "full")
        items.append(_item("upstream", f"{i}:{a.phase}:{a.type}", f"[Stage {a.phase}] {a.type}: {a.title}", chars=kept, total=len(body),
                           status=status, kind="artifact",
                           source={"type": "artifact", "phase": a.phase, "artifactType": a.type, "title": a.title},
                           note=f"Kept the first {kept:,} of {len(body):,} characters" if status == "condensed" else ""))

    instruction = str(overlay.get("promptOverlay") or "").split("## Production scope")[0].strip()
    items.append(_item("input", "instruction", "Reviewer instructions", chars=len(instruction),
                       status="full" if instruction else "excluded", source={"type": "plan", "field": "promptOverlay"},
                       note="" if instruction else "None given - the stage's defaults apply"))

    for a in attached:
        layer_id = "revision" if a["kind"] in ("revision", "amendment") else "attached"
        items.append(_item(layer_id, f"{a['kind']}:{a['id']}", a["label"], chars=int(a.get("chars") or 0),
                           total=int(a.get("totalChars") or a.get("chars") or 0), status=a.get("status", "full"),
                           kind=a["kind"], source={k: v for k, v in a.items() if k in ("id", "filename", "phase", "artifactType", "summary", "type")} | {"type": a["kind"]},
                           note=a.get("note", "")))

    for i, s in enumerate(snippets):
        items.append(_item("retrieved", f"{i}:{s.get('id', i)}", str(s.get("title") or "Knowledge item"),
                           chars=len(s.get("content") or ""), kind="snippet",
                           source={"type": "knowledge", "id": s.get("id"), "origin": s.get("source"), "relevance": s.get("score")},
                           note=f"relevance {s.get('score')}"))

    layers = []
    for lid, label, hint in LAYERS:
        mine = [i for i in items if i["layer"] == lid]
        layers.append({"id": lid, "label": label, "hint": hint, "items": mine,
                       "chars": sum(i["chars"] for i in mine if i["status"] != "excluded"),
                       "tokens": sum(i["tokens"] for i in mine if i["status"] != "excluded")})
    total_chars = sum(layer["chars"] for layer in layers)

    outputs = [{"id": f"output:{o}", "type": o} for o in (stage.get("outputs") or [])]
    edges: list[dict[str, Any]] = []
    for i in items:
        if i["status"] == "excluded":
            continue
        edges.append({"id": f"{i['id']}->prompt", "from": i["id"], "to": "prompt", "label": _edge_label(i)})
    for o in outputs:
        edges.append({"id": f"prompt->{o['id']}", "from": "prompt", "to": o["id"], "label": "produces"})
    for t, f in formats.items():
        src = f.get("source")
        target = next((o["id"] for o in outputs if _norm(o["type"]) == _norm(t)), None)
        if not target or src not in ("attachment", "formwork") or not f.get("refId"):
            continue
        ref = next((i for i in items if i["source"].get("id") == f["refId"] and i["kind"] in ("attachment", "template")), None)
        if ref:
            edges.append({"id": f"{ref['id']}->{target}", "from": ref["id"], "to": target,
                          "label": "layout followed" if src == "attachment" else "template followed"})

    return {
        "mode": mode, "phase": phase, "stage": stage.get("name") or "", "generatedAt": (generated_at or datetime.now(timezone.utc)).isoformat(),
        "layers": layers, "outputs": outputs, "edges": edges,
        "totals": {"chars": total_chars, "tokens": tokens_of(total_chars), "items": len(items),
                   "condensed": sum(1 for i in items if i["status"] in ("condensed", "summarised")),
                   "excluded": sum(1 for i in items if i["status"] == "excluded")},
    }


def _norm(s: str) -> str:
    return "".join(c for c in (s or "").upper() if c.isalnum())


def _edge_label(i: dict[str, Any]) -> str:
    if i["status"] in ("condensed", "summarised"):
        return i["status"]
    return {"upstream": "builds on", "attached": "analysed", "retrieved": "informs", "canon": "must follow",
            "instructions": "governs", "project": "applies", "input": "asked for", "revision": "amends"}.get(i["layer"], "")


def diff_manifests(old: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """What changed in the stage's context between two runs, by item id."""
    if not old:
        return {"added": [], "removed": [], "changed": []}
    a = {i["id"]: i for layer in old["layers"] for i in layer["items"]}
    b = {i["id"]: i for layer in new["layers"] for i in layer["items"]}
    return {
        "added": [{"id": k, "label": b[k]["label"]} for k in b if k not in a],
        "removed": [{"id": k, "label": a[k]["label"]} for k in a if k not in b],
        "changed": [{"id": k, "label": b[k]["label"], "from": {"status": a[k]["status"], "chars": a[k]["chars"]},
                     "to": {"status": b[k]["status"], "chars": b[k]["chars"]}}
                    for k in b if k in a and (a[k]["status"], a[k]["chars"]) != (b[k]["status"], b[k]["chars"])],
    }
