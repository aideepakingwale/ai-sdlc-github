"""Externalised prompt templates: every LLM prompt is a `.md` file in
the prompts directory so expert prompt engineers can tune wording WITHOUT
touching application code — edit the file, restart (or hot-reload) the service,
and the new prompt is live. Mirrors the markdown skill-pack pattern.

Frontmatter contract (validated fail-fast at boot — a broken template stops the
service rather than silently sending a malformed prompt):

    ---
    id: phase.quality.1            # unique dotted id used by render()
    version: 3                     # bump when you change the body
    description: one-line summary for the governance UI
    variables: [tech_stack]        # optional: ${...} placeholders the body uses
    ---
    (the prompt body; placeholders are ${var} — literal { } need no escaping)

Placeholders use `string.Template` syntax (`${name}`), chosen over str.format so
JSON-heavy prompts keep their literal braces unescaped. `render()` fails loudly
on a missing variable, so prompts never silently degrade.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from string import Template
from typing import Any

import yaml

log = logging.getLogger("prompts")

REQUIRED_KEYS = {"id", "version", "description"}
# A valid ${...} placeholder identifier in a body.
_PLACEHOLDER = re.compile(r"(?<!\$)\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class PromptPackError(ValueError):
    pass


def default_prompts_dir() -> Path:
    """PROMPTS_DIR env wins (mount a tuned set with no rebuild); otherwise
    resolve relative to the package (repo: services/orchestrator-py/prompts)
    with the container workdir (/app/prompts) and cwd as fallbacks."""
    env_dir = os.environ.get("PROMPTS_DIR")
    candidates = [
        *([Path(env_dir)] if env_dir else []),
        Path(__file__).resolve().parents[2] / "prompts",
        Path("/app/prompts"),
        Path.cwd() / "prompts",
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[-1]


def parse_prompt_markdown(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        raise PromptPackError(f"{path.name}: missing YAML frontmatter")
    try:
        _, fm, body = raw.split("---", 2)
    except ValueError as err:
        raise PromptPackError(f"{path.name}: malformed frontmatter fences") from err
    try:
        meta = yaml.safe_load(fm) or {}
    except yaml.YAMLError as err:
        raise PromptPackError(f"{path.name}: invalid YAML frontmatter: {err}") from err

    missing = REQUIRED_KEYS - set(meta)
    if missing:
        raise PromptPackError(f"{path.name}: missing frontmatter keys {sorted(missing)}")
    if not isinstance(meta["version"], int) or meta["version"] < 1:
        raise PromptPackError(f"{path.name}: version must be a positive integer")
    template = body.strip()
    if not template:
        raise PromptPackError(f"{path.name}: empty prompt body")

    # Validate the template compiles and that declared variables match usage, so
    # a typo in a placeholder is caught at boot, not at the first LLM call.
    try:
        Template(template)
    except ValueError as err:
        raise PromptPackError(f"{path.name}: invalid ${{...}} template: {err}") from err
    used = set(_PLACEHOLDER.findall(template))
    declared = set(meta.get("variables") or [])
    if declared and used - declared:
        raise PromptPackError(
            f"{path.name}: body uses undeclared variables {sorted(used - declared)} "
            f"(declared: {sorted(declared)})"
        )
    return {
        "id": str(meta["id"]),
        "version": int(meta["version"]),
        "description": str(meta["description"]),
        "variables": sorted(used),
        "template": template,
        "file": path.name,
    }


def load_prompt_packs(directory: Path | None = None) -> list[dict[str, Any]]:
    directory = directory or default_prompts_dir()
    if not directory.is_dir():
        raise PromptPackError(f"prompts directory not found: {directory}")
    packs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(directory.glob("*.md")):
        pack = parse_prompt_markdown(path)
        if pack["id"] in seen:
            raise PromptPackError(f"{path.name}: duplicate prompt id '{pack['id']}'")
        seen.add(pack["id"])
        packs.append(pack)
    if not packs:
        raise PromptPackError(f"no prompt templates found in {directory}")
    log.info("loaded %d prompt templates from %s", len(packs), directory)
    return packs
