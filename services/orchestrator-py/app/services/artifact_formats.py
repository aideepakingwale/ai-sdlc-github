"""Per-artifact output format.

A stage produces several artifacts (PRD, epics, HLD, ADRs, OpenAPI ...). Each one can have its
own *layout source* and its own *delivered file type*, instead of one choice for the whole stage:

  layout source   system      the platform's standard template (plus any house template, below)
                  attachment  follow the structure (headings, order, tables) of an attached document
                  formwork    follow a saved output template for that artifact type

  delivered as    the file type the artifact is exported / downloaded as (docx, pdf, json ...)

What a type allows depends on its *kind*: prose documents can follow a document or template;
structured records and code can follow a template but not a prose layout; diagrams are fixed.

Stored per stage in `stage_plans.artifact_formats` as
    {"PRD": {"source": "attachment", "refId": "<attachment id>", "fileType": "docx"}, ...}
An artifact with no entry uses the system standard, so existing plans are unaffected.
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..domain.errors import SdlcError

SYSTEM, ATTACHMENT, FORMWORK = "system", "attachment", "formwork"
SOURCES = (SYSTEM, ATTACHMENT, FORMWORK)

NARRATIVE, STRUCTURED, CODE, DIAGRAM = "narrative", "structured", "code", "diagram"

# Artifact type -> kind. Types not listed are treated as NARRATIVE: custom stages produce prose deliverables.
TYPE_KINDS: dict[str, str] = {
    **dict.fromkeys(("PRD", "HLD", "LLD", "TEST_STRATEGY", "RTM", "PIPELINE_DESIGN", "DOCUMENT"), NARRATIVE),
    **dict.fromkeys(("EPIC", "FEATURE", "USER_STORY", "ADR", "XRAY_TESTS", "PULL_REQUEST"), STRUCTURED),
    **dict.fromkeys(("OPENAPI", "DBML", "CDK", "K6_SCRIPT", "POSTMAN_COLLECTION", "GITHUB_ACTIONS", "DOCKERFILE",
                     "STRUCTURIZR_DSL", "PLANTUML", "GRAFANA_DASHBOARD", "APP_CODE", "UNIT_TESTS"), CODE),
    **dict.fromkeys(("CLOUDCRAFT_JSON", "ARCH_DIAGRAM", "COMPONENT_DIAGRAM", "LLD_DIAGRAM", "HLD_DIAGRAM"), DIAGRAM),
}

ALLOWED_SOURCES: dict[str, tuple[str, ...]] = {
    NARRATIVE: (SYSTEM, ATTACHMENT, FORMWORK),
    STRUCTURED: (SYSTEM, FORMWORK),
    CODE: (SYSTEM, FORMWORK),
    DIAGRAM: (SYSTEM,),
}

# What the content is natively, and the file types it can be delivered as. Conversions are
# deterministic (no model): a markdown document renders to docx / pdf / html; OpenAPI converts
# between YAML and JSON.
NATIVE_FILE_TYPE: dict[str, str] = {
    NARRATIVE: "markdown", STRUCTURED: "markdown", CODE: "text", DIAGRAM: "svg",
    "OPENAPI": "yaml", "DBML": "dbml", "CDK": "typescript", "K6_SCRIPT": "javascript",
    "POSTMAN_COLLECTION": "json", "GITHUB_ACTIONS": "yaml", "DOCKERFILE": "dockerfile",
    "STRUCTURIZR_DSL": "dsl", "PLANTUML": "plantuml", "GRAFANA_DASHBOARD": "json",
}
EXTRA_FILE_TYPES: dict[str, tuple[str, ...]] = {
    NARRATIVE: ("docx", "pdf", "html"),
    "OPENAPI": ("json",),
}

FILE_LABELS = {"markdown": "Markdown", "docx": "Word (.docx)", "pdf": "PDF", "html": "HTML", "yaml": "YAML",
               "json": "JSON", "text": "Plain text", "svg": "SVG", "dbml": "DBML", "typescript": "TypeScript",
               "javascript": "JavaScript", "dockerfile": "Dockerfile", "dsl": "DSL", "plantuml": "PlantUML"}


def norm_type(name: str) -> str:
    """'Epic & Story backlog' / 'openapi' -> a stable key ('EPIC_STORY_BACKLOG' / 'OPENAPI')."""
    return re.sub(r"[^A-Z0-9]+", "_", (name or "").upper()).strip("_")


def kind_of(output: str) -> str:
    return TYPE_KINDS.get(norm_type(output), NARRATIVE)


def native_file_type(output: str) -> str:
    t = norm_type(output)
    return NATIVE_FILE_TYPE.get(t) or NATIVE_FILE_TYPE[kind_of(output)]


def file_types_for(output: str) -> list[str]:
    """Every file type this artifact can be delivered as, the native one first."""
    t = norm_type(output)
    extra = EXTRA_FILE_TYPES.get(t) or EXTRA_FILE_TYPES.get(kind_of(output), ())
    return [native_file_type(output), *extra]


def sources_for(output: str) -> list[str]:
    return list(ALLOWED_SOURCES[kind_of(output)])


def parse_formats(raw: Any) -> dict[str, dict[str, str]]:
    """Tolerant read of a stored value (jsonb may arrive as a string): malformed entries are dropped."""
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for key, val in raw.items():
        if not isinstance(val, dict):
            continue
        entry = {k: str(val[k]) for k in ("source", "refId", "fileType") if val.get(k)}
        if entry.get("source") in SOURCES or entry.get("fileType"):
            out[norm_type(str(key))] = entry
    return out


def validate_formats(raw: Any, allowed_outputs: list[str]) -> dict[str, dict[str, str]]:
    """Strict check of what a reviewer submits; raises VALIDATION_FAILED naming the problem.

    Only artifacts the stage can actually produce are accepted, the source must be one that
    artifact's kind supports, a non-system source needs a reference, and the file type must be
    deliverable for that artifact. (Whether a referenced attachment / template belongs to this
    project is checked by the caller, which has the database.)"""
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise SdlcError("VALIDATION_FAILED", "artifactFormats must be an object keyed by artifact type")
    allowed = {norm_type(o): o for o in allowed_outputs}
    clean: dict[str, dict[str, str]] = {}
    for key, val in raw.items():
        t = norm_type(str(key))
        if t not in allowed:
            raise SdlcError("VALIDATION_FAILED", f"'{key}' is not an artifact this stage produces")
        if not isinstance(val, dict):
            raise SdlcError("VALIDATION_FAILED", f"the format for {t} must be an object")
        source = str(val.get("source") or SYSTEM)
        if source not in sources_for(t):
            raise SdlcError("VALIDATION_FAILED",
                            f"{t} ({kind_of(t)}) cannot use '{source}' as its layout - allowed: {', '.join(sources_for(t))}")
        entry: dict[str, str] = {"source": source}
        if source != SYSTEM:
            if not val.get("refId"):
                raise SdlcError("VALIDATION_FAILED", f"{t}: choose which {'attached file' if source == ATTACHMENT else 'template'} to follow")
            entry["refId"] = str(val["refId"])
        file_type = str(val.get("fileType") or "")
        if file_type:
            if file_type not in file_types_for(t):
                raise SdlcError("VALIDATION_FAILED",
                                f"{t} cannot be delivered as '{file_type}' - available: {', '.join(file_types_for(t))}")
            if file_type != native_file_type(t):
                entry["fileType"] = file_type              # the native type is the default: not stored
        if entry != {"source": SYSTEM}:
            clean[t] = entry
    return clean


def attachment_layout_types(formats: dict[str, dict[str, str]], included: list[str] | None = None) -> list[str]:
    """Artifact types that must be written as their own document following an attached file.
    Only prose artifacts qualify, and only those the reviewer kept in scope."""
    keep = {norm_type(i) for i in included} if included is not None else None
    return [t for t, f in formats.items()
            if f.get("source") == ATTACHMENT and kind_of(t) == NARRATIVE and (keep is None or t in keep)]


def formwork_selection(formats: dict[str, dict[str, str]]) -> dict[str, str]:
    """{artifact type: formwork id} for artifacts the reviewer pointed at a specific template."""
    return {t: f["refId"] for t, f in formats.items() if f.get("source") == FORMWORK and f.get("refId")}


def catalog(outputs: list[str]) -> list[dict[str, Any]]:
    """Per output: its kind and what layout sources / file types it supports."""
    out = []
    seen: set[str] = set()
    for o in outputs:
        t = norm_type(o)
        if not t or t in seen:
            continue
        seen.add(t)
        out.append({"output": o, "type": t, "kind": kind_of(t), "sources": sources_for(t),
                    "fileTypes": [{"value": f, "label": FILE_LABELS.get(f, f), "native": i == 0}
                                  for i, f in enumerate(file_types_for(t))]})
    return out


def convert_file(artifact_type: str, content: str, filename: str, target: str) -> tuple[str, str]:
    """Deterministic (no model) conversion of an artifact's content to the requested delivered file
    type. Only OPENAPI converts (YAML <-> JSON); everything else is returned unchanged. Returns
    (content, filename). Raises VALIDATION_FAILED for a type this artifact cannot be delivered as."""
    t = norm_type(artifact_type)
    already = filename.lower().endswith((".yaml", ".yml") if target == "yaml" else (".json",))
    if not target or (target == native_file_type(t) and (t != "OPENAPI" or already)):
        return content, filename
    if target not in file_types_for(t):
        raise SdlcError("VALIDATION_FAILED", f"{t} cannot be delivered as '{target}'")
    if t == "OPENAPI" and target in ("json", "yaml"):
        import yaml
        try:
            doc = yaml.safe_load(content)
        except yaml.YAMLError as err:
            raise SdlcError("VALIDATION_FAILED", f"the spec is not valid YAML/JSON, so it cannot be converted: {err}") from err
        stem = filename.rsplit(".", 1)[0] if "." in filename else filename
        if target == "json":
            return json.dumps(doc, indent=2, ensure_ascii=False) + "\n", f"{stem}.json"
        return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), f"{stem}.yaml"
    return content, filename          # docx / pdf / html are rendered in the browser from the document view
