"""Intelligent Context Window (Module 3): approved artifacts from phase N feed
phase N+1; over the token threshold, non-exact bodies are LLM-summarised
(oldest phase first). `exact` artifacts (OpenAPI/LLD/CDK) stay verbatim."""

from __future__ import annotations

from ..domain.models import ContextArtifact, estimate_tokens
from ..integrations.llm import LlmClient
from .prompt_library import render as render_prompt


def _render(items: list[ContextArtifact]) -> str:
    return "\n\n".join(
        f"### [Phase {a.phase}] {a.type}: {a.title}\n{a.content or a.summary}" for a in items
    )


async def build_context_block(
    artifacts: list[ContextArtifact], threshold_tokens: int, llm: LlmClient
) -> tuple[str, bool]:
    block = _render(artifacts)
    if estimate_tokens(block) <= threshold_tokens:
        return block, False

    working = [a.model_copy(deep=True) for a in artifacts]

    async def _summarise(artifact: ContextArtifact) -> None:
        try:
            res = await llm.generate(
                intent="standard", tag="context_compression", temperature=0.1, max_tokens=512,
                messages=[
                    {"role": "system", "content": render_prompt("context.compression.system")},
                    {"role": "user", "content": f"{artifact.type}: {artifact.title}\n\n{(artifact.content or '')[:16_000]}"},
                ],
            )
            artifact.summary = res.content.strip()
        except Exception:
            artifact.summary = f"{artifact.summary} (body elided under token pressure)"
        artifact.content = None

    # Pass 1: summarise non-exact bodies (oldest phase first) — the usual case.
    for artifact in sorted(
        (a for a in working if not a.exact and a.content and len(a.content) > 400),
        key=lambda a: a.phase,
    ):
        await _summarise(artifact)
        block = _render(working)
        if estimate_tokens(block) <= threshold_tokens:
            return block, True

    # Pass 2: under heavy pressure, shrink EXACT bodies too — deterministically
    # (no extra LLM calls, which could trip the provider's rate limit and force
    # the main generation onto the mock). The verbatim artifact is preserved in
    # the content store; here it is only upstream context, so keeping a head
    # excerpt keeps the prompt within a free-tier token budget (e.g. Groq's 12k
    # TPM) so real generation succeeds instead of 413-ing.
    for artifact in sorted(
        (a for a in working if a.exact and a.content and len(a.content) > 400),
        key=lambda a: a.phase,
    ):
        head = (artifact.content or "")[:600]
        artifact.summary = f"{artifact.summary} — excerpt:\n{head}\n… (body elided for context budget)"
        artifact.content = None
        block = _render(working)
        if estimate_tokens(block) <= threshold_tokens:
            return block, True

    # Last resort: hard-clamp so the block can never blow the token budget,
    # keeping the newest phases (most relevant) and dropping the oldest tail.
    block = _render(working)
    max_chars = threshold_tokens * 4
    if len(block) > max_chars:
        block = block[-max_chars:]
    return block, True
