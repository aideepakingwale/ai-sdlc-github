"""Live container-log streaming (D-92) via the read-only docker-socket-proxy.

The orchestrator NEVER touches the raw Docker socket. A `tecnativa/docker-socket-proxy`
sidecar (CONTAINERS=1, POST=0) exposes only GET /containers/* — so this module can
list containers and read logs, but cannot start/stop/exec/create anything.

Docker's log stream is multiplexed (non-TTY): 8-byte frame header
[stream(1)][0][0][0][size(4, big-endian)] followed by `size` payload bytes. We
de-frame and re-emit as SSE `data:` lines. A raw (TTY) fallback handles the rare
TTY-enabled container.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

# Only these compose services may be tailed from the UI.
ALLOWED_SERVICES = frozenset({
    "orchestrator", "ai-client", "tool-connector", "frontend",
    "postgres", "redis", "dynamodb", "localstack", "migrate", "plantuml", "drawio",
})


async def _resolve_container_id(cli: httpx.AsyncClient, proxy: str, service: str) -> str | None:
    """Find the container for a compose service via its compose label."""
    r = await cli.get(f"{proxy}/containers/json", params={"all": "true"})
    r.raise_for_status()
    for c in r.json():
        labels = c.get("Labels") or {}
        if labels.get("com.docker.compose.service") == service:
            return c.get("Id")
    return None


def _emit(line: str) -> str:
    return f"data: {line}\n\n"


async def stream(proxy: str, service: str, tail: int) -> AsyncIterator[str]:
    """Yield SSE frames of the service's live logs. Best-effort: any error is
    surfaced as a data line rather than raising into the response."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None)) as cli:
            cid = await _resolve_container_id(cli, proxy, service)
            if not cid:
                yield _emit(f"[no container found for service '{service}']")
                return
            params = {
                "stdout": "true", "stderr": "true", "follow": "true",
                "tail": str(tail), "timestamps": "false",
            }
            async with cli.stream("GET", f"{proxy}/containers/{cid}/logs", params=params) as resp:
                if resp.status_code != 200:
                    await resp.aread()
                    yield _emit(f"[log stream error: HTTP {resp.status_code}]")
                    return
                buf = b""
                async for chunk in resp.aiter_bytes():
                    if not chunk:
                        continue
                    buf += chunk
                    while buf:
                        # Multiplexed frame?  header[0] is stream type 1/2, [1:4]==0.
                        if len(buf) >= 8 and buf[0] in (0, 1, 2) and buf[1:4] == b"\x00\x00\x00":
                            size = int.from_bytes(buf[4:8], "big")
                            if len(buf) < 8 + size:
                                break
                            payload, buf = buf[8:8 + size], buf[8 + size:]
                            for line in payload.decode("utf-8", "replace").splitlines():
                                yield _emit(line)
                        else:
                            # Raw/TTY stream: emit complete lines only.
                            nl = buf.find(b"\n")
                            if nl < 0:
                                break
                            line, buf = buf[:nl], buf[nl + 1:]
                            yield _emit(line.decode("utf-8", "replace"))
    except Exception as err:  # noqa: BLE001 — surface, don't crash the response
        yield _emit(f"[log stream closed: {err}]")
