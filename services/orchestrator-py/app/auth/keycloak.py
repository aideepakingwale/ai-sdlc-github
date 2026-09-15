"""Keycloak OIDC confidential client (, BFF): ROPC for the SPA form and smoke
tests, Authorization Code for SSO. Tokens verified against the realm JWKS and
never forwarded to the browser."""

from __future__ import annotations

import logging
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient
from pydantic import BaseModel

from ..config import Settings
from ..domain.errors import SdlcError
from ..domain.models import Role, primary_role

log = logging.getLogger("keycloak")


class KcIdentity(BaseModel):
    sub: str
    email: str
    display_name: str
    role: Role
    roles: list[str]


class KeycloakAuth:
    def __init__(self, settings: Settings) -> None:
        if not settings.KEYCLOAK_INTERNAL_URL or not settings.KEYCLOAK_CLIENT_SECRET:
            raise SdlcError("INTERNAL", "AUTH_MODE=keycloak requires KEYCLOAK_INTERNAL_URL and KEYCLOAK_CLIENT_SECRET")
        self._settings = settings
        self.internal_realm = f"{settings.KEYCLOAK_INTERNAL_URL}/realms/{settings.KEYCLOAK_REALM}"
        public_base = settings.KEYCLOAK_PUBLIC_URL or settings.KEYCLOAK_INTERNAL_URL
        self.public_realm = f"{public_base}/realms/{settings.KEYCLOAK_REALM}"
        self._jwks = PyJWKClient(f"{self.internal_realm}/protocol/openid-connect/certs", cache_keys=True)
        self._http = httpx.AsyncClient(timeout=10.0)

    async def close(self) -> None:
        await self._http.aclose()

    async def password_login(self, email: str, password: str) -> KcIdentity:
        try:
            res = await self._http.post(
                f"{self.internal_realm}/protocol/openid-connect/token",
                data={
                    "grant_type": "password",
                    "client_id": self._settings.KEYCLOAK_CLIENT_ID,
                    "client_secret": self._settings.KEYCLOAK_CLIENT_SECRET,
                    "username": email,
                    "password": password,
                    "scope": "openid profile email",
                },
            )
        except httpx.HTTPError as err:
            raise SdlcError("PROVIDER_ERROR", f"Keycloak unreachable: {err}") from err
        if res.status_code in (400, 401):
            raise SdlcError("AUTH_FAILED", "Invalid email or password")
        if res.status_code != 200:
            raise SdlcError("PROVIDER_ERROR", f"Keycloak token endpoint returned {res.status_code}")
        return self._verify(res.json()["access_token"])

    def authorization_url(self, state: str, redirect_uri: str) -> str:
        params = urlencode({
            "client_id": self._settings.KEYCLOAK_CLIENT_ID,
            "response_type": "code",
            "scope": "openid profile email",
            "redirect_uri": redirect_uri,
            "state": state,
        })
        return f"{self.public_realm}/protocol/openid-connect/auth?{params}"

    async def exchange_code(self, code: str, redirect_uri: str) -> KcIdentity:
        res = await self._http.post(
            f"{self.internal_realm}/protocol/openid-connect/token",
            data={
                "grant_type": "authorization_code",
                "client_id": self._settings.KEYCLOAK_CLIENT_ID,
                "client_secret": self._settings.KEYCLOAK_CLIENT_SECRET,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        if res.status_code != 200:
            log.warning("code exchange failed: %s", res.status_code)
            raise SdlcError("AUTH_FAILED", "SSO code exchange failed")
        return self._verify(res.json()["access_token"])

    def _verify(self, token: str) -> KcIdentity:
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=[self.internal_realm, self.public_realm],
                options={"verify_aud": False},
            )
        except jwt.PyJWTError as err:
            log.warning("token verification failed: %s", err)
            raise SdlcError("AUTH_FAILED", "Token verification failed") from err

        roles = [r for r in payload.get("realm_access", {}).get("roles", []) if isinstance(r, str)]
        role = primary_role(roles)
        if role is None:
            raise SdlcError("FORBIDDEN", "Account has no AI-SDLC platform role assigned in Keycloak")
        email = str(payload.get("email", "")).lower()
        if not payload.get("sub") or not email:
            raise SdlcError("AUTH_FAILED", "Token missing subject or email claim")
        display_name = str(payload.get("name") or email.split("@")[0])
        return KcIdentity(sub=payload["sub"], email=email, display_name=display_name, role=role, roles=roles)
