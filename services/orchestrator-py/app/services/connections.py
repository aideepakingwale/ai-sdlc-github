"""Per-project connections: each project's own Git repository, Jira project, Confluence space and knowledge-base settings.

Settings the screen may show live in `settings`; the credential (a token) is stored encrypted and is write-only - the API only
says whether one is set. A project with no credential of its own keeps using the platform's shared connection. Every kind can be
tested: the server makes the same calls a publish would, with the credential that would be used, and reports each check.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import logging
import re
import socket
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken

from ..domain.errors import SdlcError
from ..domain.models import UserPublic

log = logging.getLogger(__name__)

KINDS = ("github", "jira", "confluence", "kb")
SECRET_FIELD = {"github": "token", "jira": "apiToken", "confluence": "apiToken"}
REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
SPACE = re.compile(r"^[A-Za-z0-9~][A-Za-z0-9_-]{0,254}$")
JIRA_KEY = re.compile(r"^[A-Z][A-Z0-9_]{1,19}$")
BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
DEFAULT_GITHUB_API = "https://api.github.com"
KB_DEFAULTS = {"standards": True, "artefacts": True, "codebase": True, "topK": 4}


def _fernet(settings: Any) -> Fernet:
    raw = getattr(settings, "CONNECTIONS_KEY", None) or f"{settings.JWT_SECRET}:project-connections"
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest()))


def host_is_safe(url: str, allow_private: bool) -> str | None:
    """None when the server may call this URL, else why not (SSRF guard: no loopback / private / link-local targets)."""
    try:
        u = urlparse(url)
    except ValueError:
        return "That is not a valid address"
    if u.scheme not in ("https", "http") or not u.hostname:
        return "Use a full address such as https://your-company.atlassian.net"
    if u.scheme == "http" and not allow_private:
        return "Use https://"
    if u.username or u.password:
        return "Do not put a user name or password in the address"
    if allow_private:
        return None
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return f"Could not find {u.hostname}"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return "That address is on a private network, which this server is not allowed to call"
    return None


def clean_settings(kind: str, raw: dict) -> dict:
    """Validate and normalise what the screen sends."""
    def text(k: str, limit: int = 300) -> str:
        return str(raw.get(k) or "").strip()[:limit]

    def url(k: str) -> str:
        v = text(k).rstrip("/")
        if v and not re.match(r"^https?://[^\s]+$", v):
            raise SdlcError("VALIDATION_FAILED", f"{k}: use a full address such as https://your-company.atlassian.net")
        return v

    out: dict[str, Any]
    if kind == "github":
        out = {"repo": text("repo", 201), "branch": text("branch", 100), "apiUrl": url("apiUrl")}
        if out["repo"] and not REPO.match(out["repo"]):
            raise SdlcError("VALIDATION_FAILED", "Repository must look like owner/name")
        if out["branch"] and not BRANCH.match(out["branch"]):
            raise SdlcError("VALIDATION_FAILED", "That is not a valid branch name")
    elif kind == "jira":
        out = {"baseUrl": url("baseUrl"), "projectKey": text("projectKey", 20).upper(), "email": text("email", 200)}
        if out["projectKey"] and not JIRA_KEY.match(out["projectKey"]):
            raise SdlcError("VALIDATION_FAILED", "A Jira project key is capital letters and digits, such as PAY")
    elif kind == "confluence":
        out = {"baseUrl": url("baseUrl"), "spaceKey": text("spaceKey", 255), "email": text("email", 200),
               "sameAsJira": bool(raw.get("sameAsJira"))}
        if out["spaceKey"] and not SPACE.match(out["spaceKey"]):
            raise SdlcError("VALIDATION_FAILED", "That is not a valid Confluence space key")
    elif kind == "kb":
        try:
            top_k = int(raw.get("topK", KB_DEFAULTS["topK"]))
        except (TypeError, ValueError):
            top_k = KB_DEFAULTS["topK"]
        out = {"standards": bool(raw.get("standards", True)), "artefacts": bool(raw.get("artefacts", True)),
               "codebase": bool(raw.get("codebase", True)), "topK": max(1, min(12, top_k))}
    else:
        raise SdlcError("VALIDATION_FAILED", f"Unknown connection '{kind}'")
    return out


class ConnectionService:
    def __init__(self, db: Any, authz: Any, audit: Any, settings: Any, rag: Any = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._db, self._authz, self._audit, self._settings, self._rag = db, authz, audit, settings, rag
        self._transport = transport          # tests inject a mock transport
        self._cache: dict[str, tuple[float, Any]] = {}

    # ------------------------------------------------------------------ authz
    async def _assert_manager(self, project_id: str, user: UserPublic) -> None:
        await self._authz.assert_project_access(project_id, user)
        project = await self._db.get_project(project_id)
        if not project:
            raise SdlcError("NOT_FOUND", "Project not found")
        if not (user.role == "SUPER_ADMIN" or (user.role == "PROJECT_MANAGER" and project["created_by"] == user.id)):
            raise SdlcError("FORBIDDEN", "Only the managing PM or a super-admin can change a project's connections")

    async def can_edit(self, project_id: str, user: UserPublic) -> bool:
        try:
            await self._assert_manager(project_id, user)
            return True
        except SdlcError:
            return False

    # ------------------------------------------------------------------ secrets
    def _seal(self, secret: str) -> str:
        return _fernet(self._settings).encrypt(secret.encode()).decode()

    def _open(self, token: str | None) -> str:
        if not token:
            return ""
        try:
            return _fernet(self._settings).decrypt(token.encode()).decode()
        except InvalidToken:
            log.warning("a stored connection credential could not be decrypted (the key changed?)")
            return ""

    # ------------------------------------------------------------------ read
    async def view(self, project_id: str, user: UserPublic) -> dict[str, Any]:
        await self._authz.assert_project_access(project_id, user)
        project = await self._db.get_project(project_id) or {}
        rows = {r["kind"]: r for r in await self._db.list_connections(project_id)}
        out: dict[str, Any] = {}
        for kind in KINDS:
            r = rows.get(kind)
            s = dict(r["settings"]) if r and isinstance(r["settings"], dict) else {}
            if kind == "github" and not s.get("repo"):
                s["repo"] = project.get("github_repo") or ""       # set when the project was created
            if kind == "jira":
                s.setdefault("projectKey", project.get("jira_project_key") or "")
                s.setdefault("baseUrl", project.get("atlassian_site_url") or "")
            if kind == "confluence":
                s.setdefault("spaceKey", project.get("confluence_space_key") or "")
            if kind == "kb":
                s = {**KB_DEFAULTS, **s}
            out[kind] = {"settings": s, "secretSet": bool(r and r.get("secret_enc")), "lastTest": r["last_test"] if r else None,
                         "updatedBy": r["updated_by"] if r else None,
                         "updatedAt": r["updated_at"].isoformat() if r and r.get("updated_at") else None}
        return {"connections": out, "canEdit": await self.can_edit(project_id, user)}

    # ------------------------------------------------------------------ write
    async def save(self, project_id: str, kind: str, user: UserPublic, payload: dict) -> dict[str, Any]:
        await self._assert_manager(project_id, user)
        if kind not in KINDS:
            raise SdlcError("VALIDATION_FAILED", f"Unknown connection '{kind}'")
        settings = clean_settings(kind, payload.get("settings") or {})
        existing = await self._db.get_connection(project_id, kind)
        sealed = existing["secret_enc"] if existing else None
        new_secret = str((payload.get("secrets") or {}).get(SECRET_FIELD.get(kind, ""), "") or "").strip()
        if payload.get("clearSecret"):
            sealed = None
        elif new_secret:
            sealed = self._seal(new_secret)
        for k in ("baseUrl", "apiUrl"):
            if settings.get(k):
                why = host_is_safe(settings[k], bool(getattr(self._settings, "CONNECTIONS_ALLOW_PRIVATE_HOSTS", False)))
                if why:
                    raise SdlcError("VALIDATION_FAILED", f"{k}: {why}")
        await self._db.upsert_connection(project_id, kind, settings, sealed, user.email)
        await self._sync_project(project_id, kind, settings)
        self._cache.pop(project_id, None)
        self._audit.record(project_id=project_id, phase=1, agent_role="Connections", event="connection.updated",
                           human_reviewer=user.email,
                           detail={"kind": kind, "settings": settings, "credentialSet": bool(sealed), "credentialChanged": bool(new_secret) or bool(payload.get("clearSecret"))})
        return (await self.view(project_id, user))["connections"][kind]

    async def _sync_project(self, project_id: str, kind: str, s: dict) -> None:
        """Keep the project's publishing target (which the tool calls already use) in step with what was saved."""
        project = await self._db.get_project(project_id) or {}
        cur = {"githubRepo": project.get("github_repo"), "atlassianSiteUrl": project.get("atlassian_site_url"),
               "jiraProjectKey": project.get("jira_project_key"), "confluenceSpaceKey": project.get("confluence_space_key")}
        if kind == "github":
            cur["githubRepo"] = s.get("repo") or None
        elif kind == "jira":
            cur["jiraProjectKey"] = s.get("projectKey") or None
            cur["atlassianSiteUrl"] = s.get("baseUrl") or None
        elif kind == "confluence":
            cur["confluenceSpaceKey"] = s.get("spaceKey") or None
        else:
            return
        await self._db.update_project_integrations(project_id, cur)

    # ------------------------------------------------------------------ what the tools use
    async def credentials_for(self, project_id: str) -> dict[str, Any]:
        """The project's own credentials, shaped for the tool connector. Empty = use the platform's shared connection."""
        import time
        hit = self._cache.get(project_id)
        if hit and time.monotonic() - hit[0] < 30:
            return hit[1]
        rows = {r["kind"]: r for r in await self._db.list_connections(project_id)}

        def conn(kind: str) -> tuple[dict, str]:
            r = rows.get(kind)
            return (dict(r["settings"]) if r and isinstance(r["settings"], dict) else {}), self._open(r["secret_enc"]) if r else ""

        out: dict[str, Any] = {}
        gs, gt = conn("github")
        if gt:
            out["github"] = {"token": gt, **({"apiUrl": gs["apiUrl"]} if gs.get("apiUrl") else {})}
        js, jt = conn("jira")
        if jt and js.get("baseUrl") and js.get("email"):
            out["jira"] = {"baseUrl": js["baseUrl"], "email": js["email"], "apiToken": jt}
        cs, ct = conn("confluence")
        if cs.get("sameAsJira") and "jira" in out:
            out["confluence"] = {**out["jira"], **({"baseUrl": cs["baseUrl"]} if cs.get("baseUrl") else {})}
        elif ct and cs.get("baseUrl") and cs.get("email"):
            out["confluence"] = {"baseUrl": cs["baseUrl"], "email": cs["email"], "apiToken": ct}
        self._cache[project_id] = (time.monotonic(), out)
        return out

    # ------------------------------------------------------------------ test
    async def test(self, project_id: str, kind: str, user: UserPublic, payload: dict | None = None) -> dict[str, Any]:
        """Run the checks. Values typed on the screen but not yet saved are tested as they are; a blank credential uses the saved one."""
        await self._assert_manager(project_id, user)
        if kind not in KINDS:
            raise SdlcError("VALIDATION_FAILED", f"Unknown connection '{kind}'")
        payload = payload or {}
        saved = await self._db.get_connection(project_id, kind)
        merged = clean_settings(kind, {**(dict(saved["settings"]) if saved and isinstance(saved["settings"], dict) else {}),
                                       **(payload.get("settings") or {})})
        typed = str((payload.get("secrets") or {}).get(SECRET_FIELD.get(kind, ""), "") or "").strip()
        secret = typed or (self._open(saved["secret_enc"]) if saved else "")
        jira_saved = await self._db.get_connection(project_id, "jira")
        checks = await self._run(kind, merged, secret, jira_saved, project_id)
        result = {"ok": bool(checks) and all(c["ok"] or c.get("optional") for c in checks), "at": datetime.now(UTC).isoformat(),
                  "by": user.email, "checks": checks, "usesOwnCredential": bool(secret)}
        if saved:
            await self._db.set_connection_test(project_id, kind, result)
        self._audit.record(project_id=project_id, phase=1, agent_role="Connections", event="connection.tested",
                           human_reviewer=user.email, detail={"kind": kind, "ok": result["ok"], "failed": [c["name"] for c in checks if not c["ok"]]})
        return result

    async def _run(self, kind: str, s: dict, secret: str, jira_saved: dict | None, project_id: str) -> list[dict]:
        if kind == "kb":
            return await self._check_kb(project_id, s)
        allow = bool(getattr(self._settings, "CONNECTIONS_ALLOW_PRIVATE_HOSTS", False))
        if kind == "github":
            api = s.get("apiUrl") or DEFAULT_GITHUB_API
            if not s.get("repo"):
                return [_c("Repository set", False, "Enter the repository as owner/name")]
            if not secret:
                return [_c("Repository set", True, s["repo"]),
                        _c("Own access token", False, "No token of your own for this project. It publishes with the platform's shared connection, which this screen cannot test. Add a token to use and test one.", optional=True)]
            if why := host_is_safe(api, allow):
                return [_c("Address allowed", False, why)]
            h = {"authorization": f"Bearer {secret}", "accept": "application/vnd.github+json", "x-github-api-version": "2022-11-28"}
            async with self._client() as cx:
                out = []
                r = await _get(cx, f"{api}/user", h)
                out.append(_c("Token accepted", r is not None and r.status_code == 200, _why(r, "Signed in as " + (r.json().get("login", "") if r is not None and r.status_code == 200 else ""))))
                if not out[0]["ok"]:
                    return out
                r = await _get(cx, f"{api}/repos/{s['repo']}", h)
                ok = r is not None and r.status_code == 200
                out.append(_c("Repository reachable", ok, _why(r, s["repo"], {404: "Not found, or the token cannot see it"})))
                if ok:
                    body = r.json()
                    out.append(_c("Can write to it", bool((body.get("permissions") or {}).get("push")), "Needed to commit generated code" if not (body.get("permissions") or {}).get("push") else "Push access"))
                    br = s.get("branch") or body.get("default_branch") or "main"
                    rb = await _get(cx, f"{api}/repos/{s['repo']}/branches/{br}", h)
                    out.append(_c(f"Branch '{br}' exists", rb is not None and rb.status_code == 200, _why(rb, "Found", {404: "Not found"})))
                return out
        base = s.get("baseUrl", "")
        email, token = s.get("email", ""), secret
        if kind == "confluence" and s.get("sameAsJira"):
            js = (dict(jira_saved["settings"]) if jira_saved and isinstance(jira_saved["settings"], dict) else {})
            email, token = js.get("email", ""), self._open(jira_saved["secret_enc"]) if jira_saved else ""
            base = base or js.get("baseUrl", "")
        key = s.get("projectKey") if kind == "jira" else s.get("spaceKey")
        if not base:
            return [_c("Address set", False, "Enter the site address, such as https://your-company.atlassian.net")]
        if not key:
            return [_c("Project set" if kind == "jira" else "Space set", False, "Enter the key")]
        if not (email and token):
            return [_c("Address set", True, base), _c("Own credentials", False, "No email and API token of your own for this project. It publishes with the platform's shared connection, which this screen cannot test.", optional=True)]
        if why := host_is_safe(base, allow):
            return [_c("Address allowed", False, why)]
        h = {"authorization": "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode(), "accept": "application/json"}
        async with self._client() as cx:
            if kind == "jira":
                r = await _get(cx, f"{base}/rest/api/3/myself", h)
                out = [_c("Credentials accepted", r is not None and r.status_code == 200, _why(r, "Signed in as " + (r.json().get("displayName", "") if r is not None and r.status_code == 200 else "")))]
                if not out[0]["ok"]:
                    return out
                r = await _get(cx, f"{base}/rest/api/3/project/{key}", h)
                out.append(_c(f"Project {key} found", r is not None and r.status_code == 200, _why(r, r.json().get("name", "") if r is not None and r.status_code == 200 else "", {404: "No such project, or you cannot see it"})))
                if out[-1]["ok"]:
                    r = await _get(cx, f"{base}/rest/api/3/mypermissions?projectKey={key}&permissions=CREATE_ISSUES", h)
                    can = bool(r is not None and r.status_code == 200 and ((r.json().get("permissions") or {}).get("CREATE_ISSUES") or {}).get("havePermission"))
                    out.append(_c("Can create issues", can, "Needed to publish epics and stories" if not can else "Allowed"))
                return out
            r = await _get(cx, f"{base}/wiki/rest/api/user/current", h)
            out = [_c("Credentials accepted", r is not None and r.status_code == 200, _why(r, "Signed in as " + (r.json().get("displayName", "") if r is not None and r.status_code == 200 else "")))]
            if not out[0]["ok"]:
                return out
            r = await _get(cx, f"{base}/wiki/rest/api/space/{key}", h)
            out.append(_c(f"Space {key} found", r is not None and r.status_code == 200, _why(r, r.json().get("name", "") if r is not None and r.status_code == 200 else "", {404: "No such space, or you cannot see it"})))
            return out

    async def _check_kb(self, project_id: str, s: dict) -> list[dict]:
        rows = await self._db.count_kb_docs(["global", project_id])
        n = {"standard": 0, "artifact": 0, "codebase": 0}
        for r in rows:
            n[r["source"]] = n.get(r["source"], 0) + int(r["n"])
        checks = [
            _c("Organisation standards", n["standard"] > 0 or not s.get("standards"), f"{n['standard']} indexed" + ("" if s.get("standards") else " (switched off for this project)")),
            _c("Approved artefacts", True, f"{n['artifact']} indexed" + ("" if s.get("artefacts") else " (switched off for this project)")),
            _c("Codebase files", True, f"{n['codebase']} indexed" + ("" if s.get("codebase") else " (switched off for this project)")),
        ]
        if self._rag is not None:
            try:
                hits = await self._rag.retrieve("architecture decisions and standards", project_id)
                checks.append(_c("Search returns results", bool(hits), f"{len(hits)} match(es) for a sample question" if hits else "Nothing matched a sample question. Nothing is indexed for this project yet."))
            except Exception as err:  # noqa: BLE001
                checks.append(_c("Search returns results", False, f"Search failed: {err}"))
        return checks

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=10, follow_redirects=False, transport=self._transport)


def _c(name: str, ok: bool, detail: str = "", optional: bool = False) -> dict:
    return {"name": name, "ok": ok, "detail": detail, **({"optional": True} if optional else {})}


async def _get(cx: httpx.AsyncClient, url: str, headers: dict) -> httpx.Response | None:
    try:
        return await cx.get(url, headers=headers)
    except httpx.HTTPError as err:
        log.info("connection test could not reach %s: %s", url.split("?")[0], type(err).__name__)
        return None


def _why(r: httpx.Response | None, ok_text: str = "", by_status: dict[int, str] | None = None) -> str:
    if r is None:
        return "Could not reach it. Check the address and the network."
    if r.status_code == 200:
        return ok_text
    if by_status and r.status_code in by_status:
        return by_status[r.status_code]
    return {401: "The credentials were refused", 403: "The credentials do not allow this", 429: "Rate limited. Try again in a minute"}.get(
        r.status_code, f"Unexpected response ({r.status_code})")


