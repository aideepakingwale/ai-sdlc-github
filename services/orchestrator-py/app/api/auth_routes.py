"""AuthN routes — contract-identical to the previous implementation."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse

from ..auth.passwords import verify_password
from ..auth.sessions import SESSION_COOKIE, sign_session
from ..domain.errors import SdlcError
from ..domain.models import LoginRequest, UserPublic
from .deps import Container, current_user, get_container

router = APIRouter(prefix="/api/auth")
OIDC_STATE_COOKIE = "sdlc_oidc_state"


async def _rate_limit_login(container: Container, ip: str, email: str) -> None:
    """5/min per (IP, account); Keycloak bruteForceProtected is the per-account layer."""
    import time

    key = f"ratelimit:login:{ip}:{email}:{int(time.time() // 60)}"
    count = await container.redis.incr(key)
    if count == 1:
        await container.redis.expire(key, 120)
    if count > 5:
        raise SdlcError("RATE_LIMITED", "Too many login attempts; retry in a minute")


def _issue_session(response: Response, container: Container, user: UserPublic) -> dict:
    token = sign_session(user, container.settings.JWT_SECRET, container.settings.SESSION_TTL_HOURS)
    response.set_cookie(
        SESSION_COOKIE, token,
        httponly=True, samesite="strict",
        secure=container.settings.NODE_ENV == "production",
        path="/", max_age=container.settings.SESSION_TTL_HOURS * 3600,
    )
    return {"user": user.model_dump()}


@router.get("/config")
async def auth_config(container: Container = Depends(get_container)) -> dict:
    keycloak_mode = container.settings.AUTH_MODE == "keycloak"
    return {"mode": container.settings.AUTH_MODE,
            "ssoLoginUrl": "/api/auth/oidc/login" if keycloak_mode else None}


@router.post("/login")
async def login(
    body: LoginRequest, request: Request, response: Response,
    container: Container = Depends(get_container),
) -> dict:
    email = body.email.lower()
    await _rate_limit_login(container, request.client.host if request.client else "unknown", email)

    if container.settings.AUTH_MODE == "keycloak":
        identity = await container.keycloak.password_login(email, body.password)
        user = await container.authz.provision_idp_user(identity)
        return _issue_session(response, container, user)

    row = await container.db.get_user_by_email(email)
    if not row or not verify_password(body.password, row["password_hash"]):
        raise SdlcError("AUTH_FAILED", "Invalid email or password")
    user = UserPublic(id=row["id"], email=row["email"], displayName=row["display_name"], role=row["role"])
    return _issue_session(response, container, user)


@router.get("/oidc/login")
async def oidc_login(container: Container = Depends(get_container)) -> RedirectResponse:
    if container.settings.AUTH_MODE != "keycloak":
        raise SdlcError("VALIDATION_FAILED", "SSO is only available when AUTH_MODE=keycloak")
    state = secrets.token_hex(24)
    redirect_uri = f"{container.settings.APP_PUBLIC_URL}/api/auth/callback"
    response = RedirectResponse(container.keycloak.authorization_url(state, redirect_uri))
    response.set_cookie(
        OIDC_STATE_COOKIE, state, httponly=True, samesite="lax",
        secure=container.settings.NODE_ENV == "production", path="/api/auth", max_age=300,
    )
    return response


@router.get("/callback")
async def oidc_callback(
    code: str, state: str, request: Request, container: Container = Depends(get_container),
) -> RedirectResponse:
    if container.settings.AUTH_MODE != "keycloak":
        raise SdlcError("VALIDATION_FAILED", "SSO is only available when AUTH_MODE=keycloak")
    expected = request.cookies.get(OIDC_STATE_COOKIE)
    if not expected or state != expected:
        raise SdlcError("AUTH_FAILED", "SSO state validation failed — restart the login flow")
    identity = await container.keycloak.exchange_code(
        code, f"{container.settings.APP_PUBLIC_URL}/api/auth/callback"
    )
    user = await container.authz.provision_idp_user(identity)
    response = RedirectResponse(container.settings.APP_PUBLIC_URL)
    response.delete_cookie(OIDC_STATE_COOKIE, path="/api/auth")
    _issue_session(response, container, user)
    return response


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def me(user: UserPublic = Depends(current_user)) -> dict:
    return {"user": user.model_dump()}
