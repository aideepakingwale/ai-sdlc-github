"""Formwork Library — the moulds that shape generated output.

In construction, formwork is the mould concrete is poured into: it does not
become the structure, it determines its shape. A Formwork here is an output
TEMPLATE bound to an (artefact type, output format) pair. Authorised users
upload templates; the platform ANALYSES each one deterministically (sections,
placeholders, format, required fields) and stores it in a shared library with
that mapping. Agents producing an artifact of that type receive the template
plus its analysis in the prompt, so generated documents land in the house
style every time.

Resolution order for a stage's artifact type:
    project-scoped formwork  →  platform-wide formwork  →  none (free-form)

Storage: the template body lives in Postgres AND is written to the content
store (`formworks/{scope}/{TYPE}-{id}{ext}`) so it is a real file in the
storage tier like every other artifact.
"""

from __future__ import annotations

import re
from typing import Any

from ..domain.errors import SdlcError
from ..domain.models import UserPublic

OUTPUT_FORMATS = ("markdown", "json", "yaml", "xml", "html", "text")

_FORMAT_EXT = {
    "markdown": ".md", "json": ".json", "yaml": ".yaml",
    "xml": ".xml", "html": ".html", "text": ".txt",
}

# {{name}} · {{ name }} · <PLACEHOLDER> · [TBD: thing] · ${name}
_PLACEHOLDER_RES = [
    re.compile(r"\{\{\s*([A-Za-z0-9_.\- ]+?)\s*\}\}"),
    re.compile(r"<([A-Z][A-Z0-9_ ]{2,})>"),
    re.compile(r"\[(?:TBD|TODO)[:\s]+([^\]]+)\]", re.I),
    re.compile(r"\$\{([A-Za-z0-9_.]+)\}"),
]


def detect_format(name: str, template: str) -> str:
    lowered = name.lower()
    for fmt, ext in _FORMAT_EXT.items():
        if lowered.endswith(ext):
            return fmt
    stripped = template.lstrip()
    if stripped.startswith(("{", "[")):
        return "json"
    if stripped.startswith("<"):
        return "xml"
    if re.search(r"^#{1,6}\s", template, re.M):
        return "markdown"
    return "text"


def analyse_template(template: str, output_format: str) -> dict[str, Any]:
    """Deterministic (non-LLM) structural analysis — reproducible and auditable,
    the same principle as the guardrail layer."""
    headings = [
        {"level": len(m.group(1)), "title": m.group(2).strip()}
        for m in re.finditer(r"^(#{1,6})\s+(.+)$", template, re.M)
    ] if output_format in ("markdown", "text") else []

    placeholders: list[str] = []
    for rx in _PLACEHOLDER_RES:
        for m in rx.finditer(template):
            token = m.group(1).strip()
            if token and token not in placeholders:
                placeholders.append(token)

    json_keys: list[str] = []
    if output_format == "json":
        try:
            import json

            parsed = json.loads(template)
            if isinstance(parsed, dict):
                json_keys = list(parsed.keys())
        except Exception:
            json_keys = sorted({m.group(1) for m in re.finditer(r'"([A-Za-z0-9_]+)"\s*:', template)})

    lines = template.splitlines()
    return {
        "format": output_format,
        "sections": [h["title"] for h in headings if h["level"] <= 2],
        "headings": headings,
        "placeholders": placeholders,
        "jsonKeys": json_keys,
        "lineCount": len(lines),
        "charCount": len(template),
        "hasTables": bool(re.search(r"^\|.+\|$", template, re.M)),
        "hasCodeBlocks": "```" in template,
    }


class FormworkService:
    def __init__(self, db: Any, authz: Any, audit: Any, content: Any, canon: Any) -> None:
        self._db = db
        self._authz = authz
        self._audit = audit
        self._content = content
        self._canon = canon  # reuses the Canon authoring RBAC

    # ------------------------------------------------------------------ CRUD
    async def list(self, project_id: str | None, user: UserPublic) -> list[dict]:
        if project_id:
            await self._authz.assert_project_access(project_id, user)
        elif user.role != "SUPER_ADMIN":
            raise SdlcError("FORBIDDEN", "The platform-wide Formwork library is SUPER_ADMIN only")
        rows = await self._db.list_formworks(project_id)
        return [self._row(r) for r in rows]

    async def upload(self, project_id: str | None, user: UserPublic, payload: dict) -> dict:
        """Accepts a template body; analyses and files it under
        (artefact type, output format). Platform-wide (project_id=None)
        uploads are SUPER_ADMIN only."""
        if project_id is None:
            if user.role != "SUPER_ADMIN":
                raise SdlcError("FORBIDDEN", "Only SUPER_ADMIN can publish platform-wide Formworks")
        else:
            await self._authz.assert_project_access(project_id, user)
            await self._canon.assert_can_author(project_id, user)

        artefact_type = (payload.get("artefactType") or "").strip().upper()
        template = payload.get("template") or ""
        name = (payload.get("name") or f"{artefact_type} template").strip()
        if not artefact_type or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,39}", artefact_type):
            raise SdlcError("VALIDATION_FAILED",
                            "artefactType must be an UPPER_SNAKE artifact type, e.g. HLD or TEST_STRATEGY")
        if len(template.strip()) < 20:
            raise SdlcError("VALIDATION_FAILED", "Template body is too short to be a useful Formwork")

        output_format = payload.get("outputFormat") or detect_format(name, template)
        if output_format not in OUTPUT_FORMATS:
            raise SdlcError("VALIDATION_FAILED", f"outputFormat must be one of {list(OUTPUT_FORMATS)}")

        analysis = analyse_template(template, output_format)

        # File the template in the storage tier alongside generated artifacts.
        scope = project_id or "platform"
        key = f"formworks/{scope}/{artefact_type}{_FORMAT_EXT[output_format]}"
        try:
            await self._content.put(key, template)
        except Exception:
            key = None  # storage is best-effort; Postgres remains the source of truth

        row = await self._db.upsert_formwork(
            project_id=project_id, artefact_type=artefact_type, output_format=output_format,
            name=name, template=template, analysis=analysis, storage_key=key, user_id=user.id,
        )
        self._audit.record(
            project_id=project_id, agent_role="Formwork", event="formwork.published",
            human_reviewer=user.email, artefact_body=template,
            detail={"artefactType": artefact_type, "outputFormat": output_format,
                    "sections": len(analysis["sections"]), "placeholders": len(analysis["placeholders"]),
                    "scope": scope},
        )
        return self._row(row)

    async def remove(self, formwork_id: str, user: UserPublic) -> None:
        row = await self._db.get_formwork(formwork_id)
        if not row:
            raise SdlcError("NOT_FOUND", "Formwork not found")
        if row["project_id"] is None:
            if user.role != "SUPER_ADMIN":
                raise SdlcError("FORBIDDEN", "Only SUPER_ADMIN can retire platform-wide Formworks")
        else:
            await self._canon.assert_can_author(row["project_id"], user)
        await self._db.deactivate_formwork(formwork_id)
        self._audit.record(
            project_id=row["project_id"], agent_role="Formwork", event="formwork.retired",
            human_reviewer=user.email, detail={"formworkId": formwork_id,
                                               "artefactType": row["artefact_type"]},
        )

    # ------------------------------------------------------------------ resolution + prompt block
    async def resolve(self, project_id: str, artefact_types: list[str]) -> list[dict]:
        """Formworks that apply to the given artifact types — project-scoped
        entries shadow platform-wide ones for the same type."""
        rows = await self._db.list_formworks(project_id)
        wanted = {t.upper() for t in artefact_types}
        chosen: dict[str, Any] = {}
        for r in rows:  # project rows sort first (NULLS LAST), so first wins
            t = r["artefact_type"]
            if t in wanted and t not in chosen:
                chosen[t] = r
        return [self._row(r) for r in chosen.values()]

    async def render_block(self, project_id: str, artefact_types: list[str]) -> str:
        formworks = await self.resolve(project_id, artefact_types)
        if not formworks:
            return ""
        lines = [
            "## OUTPUT FORMWORKS (mandatory templates for this stage's artifacts)",
            "For each artifact type below, the generated document MUST follow the template: keep the "
            "given section order and headings, fill every placeholder with real content (never leave "
            "the placeholder token), and match the stated output format. Add sections only if the "
            "template allows it.",
        ]
        for f in formworks:
            a = f["analysis"]
            lines.append(f"\n### {f['artefactType']} → {f['outputFormat']} (formwork: {f['name']})")
            if a.get("sections"):
                lines.append(f"Required sections, in order: {' → '.join(a['sections'])}")
            if a.get("placeholders"):
                lines.append(f"Placeholders to fill: {', '.join(a['placeholders'][:20])}")
            if a.get("jsonKeys"):
                lines.append(f"Required top-level keys: {', '.join(a['jsonKeys'])}")
            lines.append("Template:\n```\n" + f["template"][:4000] + "\n```")
        return "\n".join(lines)

    @staticmethod
    def _row(r: Any) -> dict:
        return {
            "id": r["id"], "projectId": r["project_id"],
            "scope": "platform" if r["project_id"] is None else "project",
            "artefactType": r["artefact_type"], "outputFormat": r["output_format"],
            "name": r["name"], "template": r["template"], "analysis": r["analysis"],
            "storageKey": r["storage_key"], "createdBy": r["created_by"],
            "createdAt": r["created_at"].isoformat(),
        }
