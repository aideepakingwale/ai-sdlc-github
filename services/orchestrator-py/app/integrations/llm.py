"""Client for the LLM-gateway microservice (ai-client-service): intent routing +
circuit breaking live there; here we add pydantic-validated JSON generation with
one repair retry (D-08)."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ..domain.errors import SdlcError
from ..services.prompt_library import render as render_prompt

log = logging.getLogger("llm")

T = TypeVar("T", bound=BaseModel)


def _looks_truncated(raw: str) -> bool:
    """Heuristic for output cut off at the token cap when the provider didn't flag it:
    non-empty content whose last non-space char isn't a JSON terminator (`}`/`]`).
    A complete JSON document ends in one of those; a mid-stream cut-off does not."""
    s = (raw or "").rstrip()
    return bool(s) and s[-1] not in ("}", "]")


def _sanitize_for_trace(messages: list[dict[str, Any]]) -> str:
    """Render the request messages for debug capture (D-104). Inline image blobs
    (base64) are dropped — they'd dwarf the actual prompt and aren't useful text."""
    safe: list[dict[str, Any]] = []
    for m in messages:
        mm = {k: v for k, v in m.items() if k != "images"}
        imgs = m.get("images")
        if imgs:
            mm["images"] = f"<{len(imgs)} image(s) omitted>"
        safe.append(mm)
    return json.dumps(safe, ensure_ascii=False, indent=2)


def parse_json_loose(raw: str) -> Any:
    """Direct parse → fenced ```json block → outermost-braces slice."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass
    first, last = raw.find("{"), raw.rfind("}")
    if 0 <= first < last:
        try:
            return json.loads(raw[first : last + 1])
        except json.JSONDecodeError:
            pass
    raise SdlcError("PROVIDER_ERROR", "LLM output was not valid JSON")


class LlmResult(BaseModel):
    provider: str
    model: str
    content: str
    usage: dict[str, int]
    attempts: list[str] = []
    tier: str = "auto"
    # D-103: the provider stopped at the output-token cap — `content` is truncated
    # (incomplete JSON). generate_json uses this to retry with a larger budget.
    truncated: bool = False


class LlmClient:
    def __init__(
        self,
        base_url: str,
        generate_timeout_seconds: float = 660.0,
        redis: Any = None,
        debug_env_default: bool = False,
        debug_max_chars: int = 200_000,
    ) -> None:
        self._base = base_url.rstrip("/")
        # The gateway streams real provider calls (Bedrock) that can run for
        # minutes on a large artifact; the generate POST must therefore outlast
        # the gateway's own provider ceiling (600s, D-96). If httpx gives up first
        # it drops the connection, which the gateway sees as a client abort and
        # cancels the in-flight Bedrock stream ("Request was aborted") — tripping
        # the breaker mid-generation (D-102). Short default for the quick GETs;
        # the long ceiling is applied per-request on generate() only.
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0))
        self._generate_timeout = httpx.Timeout(generate_timeout_seconds, connect=10.0)
        # Debug tracing (D-104): capture request/response bodies when enabled. The
        # effective switch is the runtime `llm_debug_trace` setting (read from Redis,
        # cached briefly) OR the env baseline. Bodies are capped at debug_max_chars.
        self._redis = redis
        self._debug_env = bool(debug_env_default)
        self._debug_max = max(1_000, int(debug_max_chars))
        self._debug_cache: tuple[bool, float] = (False, 0.0)
        # Observability hook (D-35): set post-construction; every generate()
        # records a span. Telemetry failures never propagate.
        self.telemetry: Any = None

    async def _debug_enabled(self) -> bool:
        """Is request/response capture on? Env baseline OR the runtime setting in
        Redis (`sdlc:settings:llm_debug_trace`), cached 5s to keep the hot path off
        Redis. Never raises — defaults to off."""
        if self._debug_env:
            return True
        if self._redis is None:
            return False
        val, at = self._debug_cache
        if time.perf_counter() - at < 5.0:
            return val
        enabled = False
        try:
            raw = await self._redis.get("sdlc:settings:llm_debug_trace")
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode()
            enabled = str(raw).strip().lower() == "true"
        except Exception:
            enabled = False
        self._debug_cache = (enabled, time.perf_counter())
        return enabled

    def _cap(self, s: str) -> str:
        return s if len(s) <= self._debug_max else s[: self._debug_max] + " …[truncated]"

    async def close(self) -> None:
        await self._http.aclose()

    async def providers(self) -> dict[str, Any]:
        """Fetch the gateway's live provider roster (D-68): mode, effectiveMock and
        per-provider {model, vision, configured, breaker}. Used to build the
        selectable model catalog. Never raises — returns an empty roster on error
        so the UI degrades gracefully."""
        try:
            res = await self._http.get(f"{self._base}/v1/providers")
            if res.status_code == 200:
                return res.json()
        except httpx.HTTPError:
            pass
        return {"mode": "auto", "effectiveMock": True, "activeProviders": [], "providers": []}

    async def generate(
        self,
        *,
        intent: str,
        messages: list[dict[str, Any]],
        json_mode: bool = False,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        tag: str | None = None,
        tier: str = "auto",
        model: str | None = None,
    ) -> LlmResult:
        started = time.perf_counter()

        # Debug capture (D-104): sanitise + cap the request once; images are dropped
        # (base64 would dwarf the prompt). Populated only when debug is enabled.
        debug = await self._debug_enabled()
        req_body = self._cap(_sanitize_for_trace(messages)) if debug else None

        async def _trace(**kw: Any) -> None:
            if self.telemetry is not None:
                await self.telemetry.record(
                    kind="llm", tag=tag, latency_ms=int((time.perf_counter() - started) * 1000),
                    request_body=req_body, **kw,
                )

        try:
            res = await self._http.post(
                f"{self._base}/v1/generate",
                json={
                    "intent": intent,
                    "messages": messages,
                    "json": json_mode,
                    "temperature": temperature,
                    "maxTokens": max_tokens,
                    "tier": tier,
                    **({"tag": tag} if tag else {}),
                    **({"model": model} if model else {}),  # per-step model override (D-68)
                },
                timeout=self._generate_timeout,  # outlast the gateway's provider ceiling (D-102)
            )
        except httpx.HTTPError as err:
            await _trace(tier=tier, status="error", error=f"ai-client unreachable: {err}")
            raise SdlcError("PROVIDER_ERROR", f"ai-client unreachable: {err}") from err
        if res.status_code != 200:
            detail = res.json().get("error", {}) if res.headers.get("content-type", "").startswith("application/json") else {}
            message = detail.get("message", f"ai-client returned {res.status_code}")
            await _trace(tier=tier, status="error", error=message)
            raise SdlcError("PROVIDER_ERROR", message)
        data = res.json()
        result = LlmResult(
            provider=data["provider"],
            model=data["model"],
            content=data["content"],
            usage={
                "promptTokens": data["usage"]["promptTokens"],
                "completionTokens": data["usage"]["completionTokens"],
            },
            attempts=data.get("attempts", []),
            tier=data.get("tier", tier),
            truncated=bool(data.get("truncated", False)),
        )
        await _trace(
            provider=result.provider, model=result.model, tier=result.tier,
            prompt_tokens=result.usage["promptTokens"],
            completion_tokens=result.usage["completionTokens"],
            response_body=self._cap(result.content) if debug else None,
        )
        return result

    async def generate_json(
        self,
        *,
        intent: str,
        messages: list[dict[str, Any]],
        schema: type[T],
        temperature: float = 0.2,
        max_tokens: int = 8192,
        tag: str | None = None,
        tier: str = "auto",
        model: str | None = None,
        max_attempts: int = 3,
    ) -> tuple[T, LlmResult]:
        """JSON-mode generation validated against `schema`, with truncation-aware retries.

        max_attempts caps the generate+validate retries (D-109). Advisory callers (the
        intelligent planner) pass 1 — one shot, no expensive repair loop — so a schema
        the model doesn't nail on the first try costs one call, not three.

        D-103: a large artifact can hit the provider's output-token cap; the returned
        JSON is then incomplete and unparseable, and a same-size repair just truncates
        again. So when the provider reports truncation (or the parse fails on
        cut-off-looking output) we ESCALATE `max_tokens` (up to HARD_MAX) before
        retrying, instead of only appending the validation errors."""
        HARD_MAX = 64_000  # Claude Sonnet's output ceiling — the growth limit
        effective_max = max_tokens
        last_issues = ""
        for attempt in range(max(1, max_attempts)):
            msgs = messages if attempt == 0 else [
                *messages,
                {"role": "user", "content": render_prompt("json_repair.user", issues=last_issues[:500])},
            ]
            result = await self.generate(
                intent=intent, messages=msgs, json_mode=True,
                temperature=temperature, max_tokens=effective_max, tag=tag, tier=tier, model=model,
            )
            # Provider says it hit the cap → grow the budget and retry before parsing
            # (parsing truncated JSON is pointless).
            if result.truncated and effective_max < HARD_MAX:
                effective_max = min(effective_max * 2, HARD_MAX)
                last_issues = "previous output was truncated at the token cap — return a COMPLETE, valid JSON document"
                log.warning("generate_json: output truncated (tag=%s); raising max_tokens to %s and retrying", tag, effective_max)
                continue
            try:
                return schema.model_validate(parse_json_loose(result.content)), result
            except (ValidationError, SdlcError) as err:
                last_issues = str(err)
                # Parse/validation failed on output that looks cut off (provider didn't
                # flag it) → treat as truncation and grow the budget for the next try.
                if _looks_truncated(result.content) and effective_max < HARD_MAX:
                    effective_max = min(effective_max * 2, HARD_MAX)
                    log.warning("generate_json: parse failed on truncated-looking output (tag=%s); raising max_tokens to %s", tag, effective_max)
        raise SdlcError("PROVIDER_ERROR", f"LLM failed to produce schema-valid JSON: {last_issues[:300]}")
