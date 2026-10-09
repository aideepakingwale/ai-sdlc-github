"""MCP client (/; multi-server): the executor's tool calls go over
Streamable HTTP. A fresh session per call keeps the client immune to connector
restarts (the stateless server pairs a new transport per request).

Beyond the in-house tool-connector, the platform can leverage external MCP
servers — the official GitHub MCP server and the Atlassian (Jira/Confluence) MCP
server — registered as additional namespaced sources. A tool on an external
server is addressed as ``<prefix>.<tool>`` (e.g. ``github.create_pull_request``,
``atlassian.jira_create_issue``); un-prefixed names route to the primary
tool-connector, so existing agent calls are unchanged.
"""

from __future__ import annotations

import inspect
import json
import logging
from dataclasses import dataclass
from typing import Any

from mcp import ClientSession

try:  # the function was renamed across mcp releases (…1.9 vs …1.28+)
    from mcp.client.streamable_http import streamablehttp_client
except ImportError:  # pragma: no cover - depends on installed mcp version
    from mcp.client.streamable_http import streamable_http_client as streamablehttp_client

from ..domain.errors import SdlcError

log = logging.getLogger("mcp")

# The transport signature also drifted across mcp releases: older builds accept a
# `headers=` kwarg directly; newer ones dropped it in favour of passing a
# pre-configured httpx client via `http_client=`. Detect once so per-server auth
# headers (external GitHub/Atlassian) keep working AND the header-less primary
# connector never receives an unsupported kwarg (which broke every tool call).
_TRANSPORT_PARAMS = set(inspect.signature(streamablehttp_client).parameters)
_ACCEPTS_HEADERS = "headers" in _TRANSPORT_PARAMS
_ACCEPTS_HTTP_CLIENT = "http_client" in _TRANSPORT_PARAMS


@dataclass
class McpServer:
    """A single MCP endpoint the client can reach. `prefix` namespaces its tools
    (None for the primary tool-connector); `headers` carries per-server auth
    (e.g. a GitHub PAT as a bearer token)."""

    name: str
    url: str
    prefix: str | None = None
    headers: dict[str, str] | None = None


# Tools that publish somewhere project-specific (a repository, a Confluence space, a Jira project).
TARGETED_PREFIXES = ("jira_", "confluence_", "github_")


class McpToolClient:
    def __init__(self, url: str, extra_servers: list[McpServer] | None = None) -> None:
        # The primary tool-connector is always server 0 and owns un-prefixed names.
        self._servers: list[McpServer] = [McpServer("tools", url)] + list(extra_servers or [])
        self._target_for: Any = None          # async (project_id) -> {"githubRepo":…, "confluenceSpaceKey":…, "jiraProjectKey":…}
        self._current_project: Any = None     # () -> project id of the run in progress, or None
        self._credentials_for: Any = None     # async (project_id) -> the project's OWN credentials (empty = the platform's shared ones)

    def bind_targets(self, target_for: Any, current_project: Any = None) -> None:
        """Make every publishing tool call carry ITS PROJECT's integration target (repository, space, Jira project).
        Without this a call falls back to the platform default, which every project would then share."""
        self._target_for, self._current_project = target_for, current_project

    def bind_credentials(self, credentials_for: Any) -> None:
        """A project may have its own Git / Jira / Confluence credentials; they ride beside the target on publishing calls."""
        self._credentials_for = credentials_for

    def _resolve(self, name: str) -> tuple[McpServer, str]:
        """Route a (possibly namespaced) tool name to its server + real tool name."""
        if "." in name:
            prefix, rest = name.split(".", 1)
            srv = next((s for s in self._servers if s.prefix == prefix), None)
            if srv is not None:
                return srv, rest
        return self._servers[0], name

    def _session(self, srv: McpServer):
        # Open a Streamable HTTP transport, adapting to the installed mcp API.
        # Auth headers (external servers only) go via `headers=` where supported,
        # else via a pre-built httpx client; the header-less primary connector is
        # opened plainly so no unsupported kwarg is ever passed.
        if srv.headers:
            if _ACCEPTS_HEADERS:
                return streamablehttp_client(srv.url, headers=srv.headers)
            if _ACCEPTS_HTTP_CLIENT:
                import httpx

                return streamablehttp_client(srv.url, http_client=httpx.AsyncClient(headers=srv.headers))
            log.warning("mcp transport cannot carry auth headers for %s; sending unauthenticated", srv.name)
        return streamablehttp_client(srv.url)

    async def list_tools(self) -> list[dict[str, str]]:
        """Aggregate tools across every reachable server; external tools are
        returned namespaced. An unreachable/disabled server is skipped, not fatal."""
        out: list[dict[str, str]] = []
        for srv in self._servers:
            try:
                async with self._session(srv) as streams:
                    read, write = streams[0], streams[1]  # 2- or 3-tuple across mcp versions
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        res = await session.list_tools()
                        for t in res.tools:
                            name = f"{srv.prefix}.{t.name}" if srv.prefix else t.name
                            out.append({"name": name, "description": t.description or "", "server": srv.name})
            except Exception as err:  # a down external server must not break the primary
                log.warning("MCP server %s (%s) unavailable: %s", srv.name, srv.url, err)
        return out

    async def _with_target(self, srv: McpServer, real_name: str, args: dict[str, Any], project_id: str | None) -> dict[str, Any]:
        """Attach the calling project's integration target to a publishing tool call (primary connector only)."""
        if srv.prefix is not None or not real_name.startswith(TARGETED_PREFIXES) or self._target_for is None or "target" in args:
            return args
        pid = project_id or (self._current_project() if self._current_project else None)
        if not pid:
            return args
        target = await self._target_for(pid)
        creds = await self._credentials_for(pid) if self._credentials_for else {}
        extra = {**({"target": target} if target else {}), **({"credentials": creds} if creds else {})}
        return {**args, **extra} if extra else args

    async def call(self, name: str, args: dict[str, Any], *, project_id: str | None = None) -> dict[str, Any]:
        srv, real_name = self._resolve(name)
        args = await self._with_target(srv, real_name, args, project_id)
        try:
            async with self._session(srv) as streams:
                read, write = streams[0], streams[1]  # 2- or 3-tuple across mcp versions
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    res = await session.call_tool(real_name, args)
        except SdlcError:
            raise
        except Exception as err:  # transport-level failure
            raise SdlcError("TOOL_ERROR", f"MCP transport failure calling {name} on {srv.name}: {err}") from err

        text = "".join(c.text for c in res.content if getattr(c, "type", "") == "text" and c.text)
        # `isError` was renamed `is_error` across mcp releases — accept either.
        is_error = getattr(res, "is_error", getattr(res, "isError", False))
        if is_error:
            raise SdlcError("TOOL_ERROR", f"MCP tool {name} failed: {text[:500] or 'unknown error'}")
        if not text:
            raise SdlcError("TOOL_ERROR", f"MCP tool {name} returned no content")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # External servers may return prose/markdown rather than JSON — wrap it
            # so callers still get a structured result instead of an error.
            return {"ok": True, "text": text}


__all__ = ["McpToolClient", "McpServer"]
