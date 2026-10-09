"""Text (ASCII / box-drawing) diagrams inside generated documents -> standard Mermaid.

Models drift into drawing architecture as ASCII art in an unlabelled code fence. That does not render,
cannot be edited, and exports to Word as monospaced text. The prompts forbid it; this is the safety net:
detect those blocks deterministically, ask the model to redraw each one as Mermaid, accept the result only
if it validates, and otherwise leave the original untouched (never lose a diagram).
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

from .content_validators import autofix_mermaid, validate_mermaid
from .agent_catalog import role_of
from .prompt_library import render as render_prompt

log = logging.getLogger("text_diagrams")

_FENCE = re.compile(r"(?ms)^(?P<indent>[ \t]*)```(?P<lang>[\w+-]*)[ \t]*\n(?P<body>.*?)^[ \t]*```[ \t]*$")
_TEXT_LANGS = {"", "text", "txt", "ascii", "plain", "plaintext", "diagram", "art"}
_CORNERS = re.compile(r"[┌┐└┘╔╗╚╝╭╮╰╯]")
_PLUS_RULE = re.compile(r"\+[-=~]{3,}\+")
_ARROW = re.compile(r"-->|──+[>▶►]|[─-]+>|<[─-]+|[▶►▼▲]|\bv\b|\^|=+>")


@dataclass
class TextDiagram:
    start: int
    end: int
    body: str


def is_text_diagram(body: str) -> bool:
    """A box-and-arrow drawing: boxes (corner glyphs or +---+ rules) AND connectors. Directory trees,
    ordinary code and plain tables do not qualify."""
    lines = [ln for ln in body.splitlines() if ln.strip()]
    if len(lines) < 3:
        return False
    boxes = len(_CORNERS.findall(body)) >= 4 or len(_PLUS_RULE.findall(body)) >= 2
    links = bool(_ARROW.search(body)) or any(set(ln.strip()) <= set("|│║ ") and ln.strip() for ln in lines)
    return boxes and links


def find_text_diagrams(markdown: str) -> list[TextDiagram]:
    out = []
    for m in _FENCE.finditer(markdown or ""):
        if m.group("lang").lower() in _TEXT_LANGS and is_text_diagram(m.group("body")):
            out.append(TextDiagram(m.start(), m.end(), m.group("body")))
    return out


async def _convert_one(llm: Any, block: TextDiagram, tag: str) -> str | None:
    try:
        res = await llm.generate(
            intent="standard", tag=tag, temperature=0, max_tokens=2_000, role=role_of("text-diagram-converter", "generate"),
            messages=[{"role": "system", "content": render_prompt("diagram.from_text.system")},
                      {"role": "user", "content": block.body[:6_000]}])
    except Exception as err:  # noqa: BLE001 - advisory: the original block stays
        log.info("text diagram conversion failed: %s", err)
        return None
    src = autofix_mermaid(getattr(res, "content", "") or "")
    return src if src and not validate_mermaid(src) else None


async def convert_text_diagrams(llm: Any, markdown: str, *, max_blocks: int = 8, tag: str = "text_diagram") -> tuple[str, dict[str, int]]:
    """Replace each text diagram with a ```mermaid block (when the redrawn source validates).
    Returns (markdown, {"found", "converted"}). Blocks beyond `max_blocks` are left as they were."""
    blocks = find_text_diagrams(markdown)
    if not blocks:
        return markdown, {"found": 0, "converted": 0}
    todo = blocks[:max_blocks]
    results = await asyncio.gather(*(_convert_one(llm, b, tag) for b in todo))
    out, pos, done = [], 0, 0
    for blk, src in zip(todo, results, strict=True):
        if not src:
            continue
        out.append(markdown[pos:blk.start])
        out.append(f"```mermaid\n{src}\n```")
        pos, done = blk.end, done + 1
    out.append(markdown[pos:])
    return "".join(out), {"found": len(blocks), "converted": done}
