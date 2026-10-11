"""FastAPI dependency wiring: the DI container assembled at startup + auth guard."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import Cookie, Depends, Request

from ..auth.sessions import verify_session
from ..config import Settings
from ..domain.errors import SdlcError
from ..domain.models import UserPublic
from ..services import usage_context


@dataclass
class Container:
    """Composition root: every layer built once in main.lifespan."""

    settings: Settings
    db: Any = None
    redis: Any = None
    dynamo: Any = None
    s3: Any = None
    audit: Any = None
    authz: Any = None
    keycloak: Any = None
    llm: Any = None
    mcp: Any = None
    rag: Any = None
    monitor: Any = None
    chat: Any = None
    gates: Any = None
    codebase: Any = None
    content: Any = None
    flow: Any = None
    skills: Any = None
    workflow: Any = None
    telemetry: Any = None
    canon: Any = None
    connections: Any = None  # per-project Git / Jira / Confluence / knowledge-base connections
    memory: Any = None   # MemoryService: confirmed project / organisation / personal memory
    formworks: Any = None
    code_gen: Any = None  # two-step code generation: structure approval, explorer view, zip
    project_config: Any = None  # projectconfig.json: the stack by layer
    stack_advisor: Any = None   # identifies the stack from a stage's documents
    rule_assist: Any = None     # drafts rules from a document; hints before a rule is saved
    rule_checker: Any = None    # checks a stage's documents against the must-rules
    pack_admin: Any = None      # the organisation's rule packs (create, import, export, hide, restore)
    rule_advisor: Any = None    # recommends rule packs from the project's profile
    agent_runs: Any = None      # a person running a custom agent on its own or inside a stage
    token_gate: Any = None      # refuses new work while a pool is at zero or below
    token_pools: Any = None     # token pools: allocations and balances
    reconciler: Any = None      # nightly comparison of the token ledger with Bedrock's invocation logs
    usage_reports: Any = None   # token usage reports over the ledger
    agent_defs: Any = None      # custom agents and skills: definitions, audit, approval, stage attachments
    code_edit: Any = None  # prompt-based code assistant: agentic edits shown as a diff, applied by a person
    gen_jobs: Any = None  # durable background stage generation (D-97 L2)
    extras: dict[str, Any] = field(default_factory=dict)


def get_container(request: Request) -> Container:
    return request.app.state.container


async def current_user(
    request: Request, sdlc_session: str | None = Cookie(default=None)
) -> UserPublic:
    container: Container = request.app.state.container
    if not sdlc_session:
        raise SdlcError("AUTH_FAILED", "Not authenticated")
    user = verify_session(sdlc_session, container.settings.JWT_SECRET)
    if not user:
        raise SdlcError("AUTH_FAILED", "Session invalid or expired")
    # Token ledger: every model call this request makes belongs to this person and, when one is open, to
    # their project (the dependency is async, so the context change carries into the endpoint and anything
    # it awaits or spawns).
    usage_context.reset(user_id=user.id, actor_type="user")     # a fresh context per request: nothing (not even "admitted") carries over from another
    project_id = request.path_params.get("project_id")   # the route checks access itself
    if not project_id:
        project_id = await _header_project(request, container, user)
    if project_id:
        usage_context.update(project_id=project_id)
    return user


_PROJECT_OK: dict[tuple[str, str], float] = {}


async def _header_project(request: Request, container: "Container", user: UserPublic) -> str | None:
    """The project the person has open (`X-Project-Id`), for calls whose URL carries none (MindDesigner design-time
    work). Tokens are charged to it, so the person must belong to it: a project they cannot access is ignored,
    never charged. Memberships are cached for a minute."""
    pid = request.headers.get("x-project-id")
    if not pid or len(pid) > 64:
        return None
    now = time.monotonic()
    if _PROJECT_OK.get((user.id, pid), 0.0) > now:
        return pid
    try:
        await container.authz.assert_project_access(pid, user)
        if not await container.db.get_project(pid):    # an administrator passes the access check for any id
            return None
    except Exception:  # noqa: BLE001 — denied or unknown: do not attribute
        return None
    _PROJECT_OK[(user.id, pid)] = now + 60.0
    if len(_PROJECT_OK) > 5000:
        _PROJECT_OK.clear()
    return pid


def require_tokens(entry: str):
    """Route dependency: refuse to START model work while the project's (or the organisation's) tokens are used up (docs/token-governance.md, phase 4).
    The task is admitted once here, so what it spawns is not re-checked and the activity in flight finishes. Reading and approving stay open."""
    async def dep(request: Request, user: UserPublic = Depends(current_user)) -> None:
        gate = request.app.state.container.token_gate
        if gate is not None:
            await gate.admit(project_id=usage_context.current().project_id, user_id=user.id, entry=entry)
    return Depends(dep)
