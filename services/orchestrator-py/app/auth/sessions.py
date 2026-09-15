"""Session cookie JWT (HS256) — byte-compatible with the previous session contract."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt

from ..domain.models import ROLE_PRIORITY, UserPublic

SESSION_COOKIE = "sdlc_session"


def sign_session(user: UserPublic, secret: str, ttl_hours: int) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": user.id,
            "email": user.email,
            "displayName": user.displayName,
            "role": user.role,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=ttl_hours)).timestamp()),
        },
        secret,
        algorithm="HS256",
    )


def verify_session(token: str, secret: str) -> UserPublic | None:
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    role = payload.get("role")
    if not payload.get("sub") or role not in ROLE_PRIORITY:
        return None
    return UserPublic(
        id=payload["sub"],
        email=str(payload.get("email", "")),
        displayName=str(payload.get("displayName", "")),
        role=role,
    )
