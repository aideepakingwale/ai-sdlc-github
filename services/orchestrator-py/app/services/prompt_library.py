"""Central prompt library ( Responsible AI; externalised).

EVERY prompt sent to an LLM by this platform is a named, versioned template —
no inline prompt strings at call sites. Since the templates live as
editable markdown files in the prompts directory (see prompt_loader), so expert
prompt engineers can tune wording WITHOUT changing application code: edit the
file, restart (or set PROMPTS_RELOAD=true to hot-reload), and the new prompt is
live. The full library is exposed read-only at `GET /api/governance/prompts`.

Conventions:
  - Templates use `string.Template` placeholders (`${name}`); `render()` fails
    loudly on a missing parameter (prompts never silently degrade). Literal
    braces `{ }` (JSON shapes) need no escaping.
  - `#mock:<kind>` directives drive the deterministic offline mock LLM;
    real providers ignore them.
  - `RESPONSIBLE_AI_POLICY` (the `policy.responsible_ai` template) is prepended
    to every agent/skill system prompt.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from string import Template

from .prompt_loader import load_prompt_packs


@dataclass(frozen=True)
class PromptTemplate:
    id: str
    version: int
    description: str
    template: str
    variables: tuple[str, ...] = ()
    file: str = ""


def _build() -> dict[str, PromptTemplate]:
    return {
        p["id"]: PromptTemplate(
            id=p["id"], version=p["version"], description=p["description"],
            template=p["template"], variables=tuple(p.get("variables", [])), file=p.get("file", ""),
        )
        for p in load_prompt_packs()
    }


# Loaded once at boot (fail-fast: a broken template stops the service). Set
# PROMPTS_RELOAD=true to re-read the files on every render for live tuning.
PROMPTS: dict[str, PromptTemplate] = _build()
_RELOAD = os.environ.get("PROMPTS_RELOAD", "").lower() in ("1", "true", "yes")


def _lookup(prompt_id: str) -> PromptTemplate:
    global PROMPTS
    if _RELOAD:
        PROMPTS = _build()
    tpl = PROMPTS.get(prompt_id)
    if tpl is None:
        raise KeyError(f"Prompt '{prompt_id}' is not in the prompt library")
    return tpl


def render(prompt_id: str, **params: object) -> str:
    """Render a library template; unknown ids or missing params raise."""
    tpl = _lookup(prompt_id)
    try:
        return Template(tpl.template).substitute(**params)
    except KeyError as err:
        raise KeyError(f"Prompt '{prompt_id}' is missing parameter {err}") from err


# The Responsible AI preamble as a module constant (back-compat for importers).
RESPONSIBLE_AI_POLICY = PROMPTS["policy.responsible_ai"].template


def inventory() -> dict:
    prompts = sorted(PROMPTS.values(), key=lambda t: t.id)
    return {
        "count": len(prompts),
        "prompts": [
            {
                "id": t.id, "version": t.version, "description": t.description,
                "variables": list(t.variables), "file": t.file, "template": t.template,
            }
            for t in prompts
        ],
    }
