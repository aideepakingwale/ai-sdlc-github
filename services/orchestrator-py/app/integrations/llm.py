"""Client for the LLM-gateway microservice (ai-client-service): intent routing +
circuit breaking live there; here we add pydantic-validated JSON generation with
one repair retry."""

from __future__ import annotations

import json
import re
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ..domain.errors import SdlcError
from ..services.prompt_library import render as render_prompt

T = TypeVar("T", bound=BaseModel)


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


class LlmClient:
    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0))
        # Observability hook: set post-construction; every generate
        # records a span. Telemetry failures never propagate.
        self.telemetry: Any = None

    async def close(self) -> None:
        await self._http.aclose()

    async def providers(self) -> dict[str, Any]:
        """Fetch the gateway's live provider roster: mode, effectiveMock and
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

        async def _trace(**kw: Any) -> None:
            if self.telemetry is not None:
                await self.telemetry.record(
                    kind="llm", tag=tag, latency_ms=int((time.perf_counter() - started) * 1000), **kw
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
                    **({"model": model} if model else {}), # per-step model override
                },
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
        )
        await _trace(
            provider=result.provider, model=result.model, tier=result.tier,
            prompt_tokens=result.usage["promptTokens"],
            completion_tokens=result.usage["completionTokens"],
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
    ) -> tuple[T, LlmResult]:
        """JSON-mode generation validated against `schema`; one repair retry with the errors appended."""
        last_issues = ""
        for attempt in range(2):
            msgs = messages if attempt == 0 else [
                *messages,
                {"role": "user", "content": render_prompt("json_repair.user", issues=last_issues[:500])},
            ]
            result = await self.generate(
                intent=intent, messages=msgs, json_mode=True,
                temperature=temperature, max_tokens=max_tokens, tag=tag, tier=tier, model=model,
            )
            try:
                return schema.model_validate(parse_json_loose(result.content)), result
            except (ValidationError, SdlcError) as err:
                last_issues = str(err)
        raise SdlcError("PROVIDER_ERROR", f"LLM failed to produce schema-valid JSON: {last_issues[:300]}")
