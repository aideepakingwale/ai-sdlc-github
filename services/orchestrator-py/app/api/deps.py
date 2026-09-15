"""FastAPI dependency wiring: the DI container assembled at startup + auth guard."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import Cookie, Request

from ..auth.sessions import verify_session
from ..config import Settings
from ..domain.errors import SdlcError
from ..domain.models import UserPublic


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
    formworks: Any = None
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
    return user
