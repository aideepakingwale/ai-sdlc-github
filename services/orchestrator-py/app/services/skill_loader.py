"""Markdown skill packs: every skill is a `.md` file in the skills
directory — YAML frontmatter declares identity, RBAC and execution wiring;
the markdown body is the skill's instruction (for LLM skills, the exact
system-prompt instruction; for engine-backed skills, operator documentation).

Frontmatter contract (validated fail-fast at boot — a bad RBAC declaration
stops the service rather than silently widening access):

    ---
    id: draft_story                # unique slug
    name: Draft user story
    description: one-liner for the UI
    phase: 1                       # 1-6 template binding; omit/null = any stage
    roles: [PO]                    # PhaseRoles allowed to execute (RBAC)
    tier: frontier # non_llm | local | frontier
    executor: llm                  # llm | builtin | mcp_run
    tools: []                      # MCP tools the skill may invoke
    mock_kind: phase1              # llm only: offline mock directive
    artifact_type: K6_SCRIPT       # mcp_run only: input artifact
    mcp_tool: k6_run_test          # mcp_run only: engine tool
    mcp_arg: script                # mcp_run only: arg field for the body
    mcp_extra_args: {}             # mcp_run only: static extra args
    needs_input: true
    input_hint: what to type
    ---
    (markdown instruction body)

Enforcement stays in SkillService (unchanged): role ∈ roles or SUPER_ADMIN
(PMs never execute), and template-stage gating.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from ..domain.models import PHASE_ROLES

log = logging.getLogger("skills")

REQUIRED_KEYS = {"id", "name", "description", "roles", "tier", "executor"}
VALID_TIERS = {"non_llm", "local", "frontier"}
VALID_EXECUTORS = {"llm", "builtin", "mcp_run"}


class SkillPackError(ValueError):
    pass


def default_skills_dir() -> Path:
    """SKILLS_DIR env wins; otherwise resolve relative to the package (repo
    layout: services/orchestrator-py/skills) with the container workdir
    (/app/skills) and cwd as fallbacks."""
    import os

    env_dir = os.environ.get("SKILLS_DIR")
    candidates = [
        *([Path(env_dir)] if env_dir else []),
        Path(__file__).resolve().parents[2] / "skills",
        Path("/app/skills"),
        Path.cwd() / "skills",
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[-1]


def parse_skill_markdown(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        raise SkillPackError(f"{path.name}: missing YAML frontmatter")
    try:
        _, fm, body = raw.split("---", 2)
    except ValueError as err:
        raise SkillPackError(f"{path.name}: malformed frontmatter fences") from err
    try:
        meta = yaml.safe_load(fm) or {}
    except yaml.YAMLError as err:
        raise SkillPackError(f"{path.name}: invalid YAML frontmatter: {err}") from err

    missing = REQUIRED_KEYS - set(meta)
    if missing:
        raise SkillPackError(f"{path.name}: missing frontmatter keys {sorted(missing)}")
    if meta["tier"] not in VALID_TIERS:
        raise SkillPackError(f"{path.name}: tier must be one of {sorted(VALID_TIERS)}")
    if meta["executor"] not in VALID_EXECUTORS:
        raise SkillPackError(f"{path.name}: executor must be one of {sorted(VALID_EXECUTORS)}")

    roles = meta.get("roles") or []
    if not isinstance(roles, list) or not roles:
        raise SkillPackError(f"{path.name}: roles must be a non-empty list (RBAC is mandatory)")
    bad = [r for r in roles if r not in PHASE_ROLES]
    if bad:
        raise SkillPackError(f"{path.name}: unknown roles {bad} — must be phase roles {sorted(PHASE_ROLES)}")

    phase = meta.get("phase")
    if phase is not None and (not isinstance(phase, int) or not 1 <= phase <= 6):
        raise SkillPackError(f"{path.name}: phase must be 1-6 or omitted")

    if meta["executor"] == "mcp_run":
        for key in ("artifact_type", "mcp_tool", "mcp_arg"):
            if not meta.get(key):
                raise SkillPackError(f"{path.name}: executor=mcp_run requires '{key}'")
    if meta["executor"] == "llm" and not body.strip():
        raise SkillPackError(f"{path.name}: llm skills need a markdown instruction body")

    return {**meta, "body": body.strip(), "file": path.name}


def load_skill_packs(directory: Path | None = None) -> list[dict[str, Any]]:
    directory = directory or default_skills_dir()
    if not directory.is_dir():
        raise SkillPackError(f"skills directory not found: {directory}")
    packs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(directory.glob("*.md")):
        pack = parse_skill_markdown(path)
        if pack["id"] in seen:
            raise SkillPackError(f"{path.name}: duplicate skill id '{pack['id']}'")
        seen.add(pack["id"])
        packs.append(pack)
    if not packs:
        raise SkillPackError(f"no skill packs found in {directory}")
    log.info("loaded %d markdown skill packs from %s", len(packs), directory)
    return packs
