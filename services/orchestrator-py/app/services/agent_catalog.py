"""Markdown agent definitions: every agent of the platform is one `.md` file in the agents directory.

Same pack convention as prompts, skills and steering: YAML frontmatter declares identity, the model role and what the agent reads;
the markdown body is the agent's instruction (for `specialist` agents, exactly what is sent to the model, minus an optional trailing
"## Notes (not sent to the model)" section) or its documentation (for `native` and `proposed` agents). Edit a file, restart (or set
AGENTS_RELOAD=true) and the change is live; no code change is needed to tune an agent.

Frontmatter (validated fail-fast at boot):

    id: prd                      # unique; must equal the file name
    name: PRD writer
    version: 1                   # bump when you change the body
    category: generator          # generator | planner | reviewer | utility
    runtime: specialist          # specialist: run by the specialist engine from this file
                                 # native:     the call is made by a Python service; the file documents it and owns its model role
                                 # proposed:   an agent we intend to build; listed, never run
    status: active               # active | proposed
    description: one line for the governance UI
    role: generate               # reason | generate | light | plan | vision | stage (follow the stage's choice)
    prompts: [clarify.system]    # prompt-library templates a native agent uses (checked to exist)
    entrypoint: app/services/chat.py::fn      # native: where the call is made

    # specialist agents only
    stage: 3                     # stage template 1-6
    kind: document               # document | diagram | structured | code | list
    fields: [lldMarkdown]        # output-schema fields it writes
    artifacts: [LLD]             # artefact types those become
    upstream: [HLD, ADR]         # artefact types from earlier stages it reads ('*' = all)
    after: [components]          # fields of THIS stage it builds on (it runs after them)
    canon: true                  # project canon and team memory
    stack: false                 # the technology stack decision
    attachments: false           # attached documents and pinned context
    steering: false              # expert steering for the stage persona
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml

from .prompt_library import PROMPTS

log = logging.getLogger("agents")

REQUIRED = {"id", "name", "version", "category", "runtime", "status", "description", "role"}
CATEGORIES = {"generator", "planner", "reviewer", "utility"}
RUNTIMES = {"specialist", "native", "proposed"}
ROLES = {"reason", "generate", "light", "plan", "vision", "stage"}
SPECIALIST_ROLES = {"reason", "generate", "light"}
KINDS = {"document", "diagram", "structured", "code", "list"}
NOTES = re.compile(r"^## Notes \(not sent to the model\)\s*$", re.M)


class AgentPackError(ValueError):
    pass


def default_agents_dir() -> Path:
    """AGENTS_DIR env wins; otherwise services/orchestrator-py/agents, the container's /app/agents, then ./agents."""
    env_dir = os.environ.get("AGENTS_DIR")
    candidates = [*([Path(env_dir)] if env_dir else []), Path(__file__).resolve().parents[2] / "agents", Path("/app/agents"), Path.cwd() / "agents"]
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[-1]


def _strlist(meta: dict, key: str, name: str) -> list[str]:
    v = meta.get(key, [])
    if v is None:
        v = []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise AgentPackError(f"{name}: '{key}' must be a list of strings")
    return v


def parse_agent_markdown(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        raise AgentPackError(f"{path.name}: missing YAML frontmatter")
    try:
        _, fm, body = raw.split("---", 2)
    except ValueError as err:
        raise AgentPackError(f"{path.name}: malformed frontmatter fences") from err
    try:
        meta = yaml.safe_load(fm) or {}
    except yaml.YAMLError as err:
        raise AgentPackError(f"{path.name}: invalid YAML frontmatter: {err}") from err
    missing = REQUIRED - set(meta)
    if missing:
        raise AgentPackError(f"{path.name}: missing frontmatter keys {sorted(missing)}")
    if meta["id"] != path.stem:
        raise AgentPackError(f"{path.name}: id '{meta['id']}' must equal the file name")
    if meta["category"] not in CATEGORIES:
        raise AgentPackError(f"{path.name}: category must be one of {sorted(CATEGORIES)}")
    if meta["runtime"] not in RUNTIMES:
        raise AgentPackError(f"{path.name}: runtime must be one of {sorted(RUNTIMES)}")
    if meta["status"] not in {"active", "proposed"}:
        raise AgentPackError(f"{path.name}: status must be active or proposed")
    if (meta["runtime"] == "proposed") != (meta["status"] == "proposed"):
        raise AgentPackError(f"{path.name}: runtime 'proposed' and status 'proposed' go together")
    if meta["role"] not in ROLES:
        raise AgentPackError(f"{path.name}: role must be one of {sorted(ROLES)}")
    prompts = _strlist(meta, "prompts", path.name)
    unknown = [p for p in prompts if p not in PROMPTS]
    if unknown:
        raise AgentPackError(f"{path.name}: prompts not in the prompt library: {unknown}")

    prompt_body, notes = body.strip(), ""
    m = NOTES.search(prompt_body)
    if m:
        prompt_body, notes = prompt_body[: m.start()].strip(), prompt_body[m.start():].split("\n", 1)[1].strip()
    if meta["runtime"] == "specialist":
        for key in ("stage", "kind", "fields", "artifacts"):
            if key not in meta:
                raise AgentPackError(f"{path.name}: specialist agents need '{key}'")
        if not isinstance(meta["stage"], int) or not 1 <= meta["stage"] <= 6:
            raise AgentPackError(f"{path.name}: stage must be 1-6")
        if meta["kind"] not in KINDS:
            raise AgentPackError(f"{path.name}: kind must be one of {sorted(KINDS)}")
        if meta["role"] not in SPECIALIST_ROLES:
            raise AgentPackError(f"{path.name}: a specialist's role must be one of {sorted(SPECIALIST_ROLES)}")
        if not _strlist(meta, "fields", path.name):
            raise AgentPackError(f"{path.name}: fields must not be empty")
        _strlist(meta, "artifacts", path.name), _strlist(meta, "upstream", path.name), _strlist(meta, "after", path.name)
        if not prompt_body:
            raise AgentPackError(f"{path.name}: a specialist needs an instruction body")
    return {**meta, "prompts": prompts, "body": prompt_body, "notes": notes, "file": path.name}


def load_agents(directory: Path | None = None) -> list[dict[str, Any]]:
    directory = directory or default_agents_dir()
    if not directory.is_dir():
        raise AgentPackError(f"agents directory not found: {directory}")
    agents: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(directory.rglob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        a = parse_agent_markdown(path)
        if a["id"] in seen:
            raise AgentPackError(f"{path.name}: duplicate agent id '{a['id']}'")
        seen.add(a["id"])
        a["path"] = path.relative_to(directory).as_posix()
        agents.append(a)
    if not agents:
        raise AgentPackError(f"no agent definitions found in {directory}")
    log.info("loaded %d agent definitions from %s", len(agents), directory)
    return agents


_CACHE: list[dict[str, Any]] | None = None
_RELOAD = os.environ.get("AGENTS_RELOAD", "").lower() in ("1", "true", "yes")


def catalog() -> list[dict[str, Any]]:
    """Every agent definition (loaded once; AGENTS_RELOAD=true re-reads the files each time)."""
    global _CACHE
    if _CACHE is None or _RELOAD:
        _CACHE = load_agents()
    return _CACHE


def get(agent_id: str) -> dict[str, Any] | None:
    return next((a for a in catalog() if a["id"] == agent_id), None)


def role_of(agent_id: str, default: str) -> str:
    """The model role an agent declares, so tuning it is an edit to its file. 'stage' (or an unknown agent) keeps `default`."""
    a = get(agent_id)
    return default if not a or a["role"] == "stage" else a["role"]
