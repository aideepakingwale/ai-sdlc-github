"""On-demand reading of an attached document: an outline, then exactly the parts asked for.

Instead of deciding what the model sees once, at upload, with a keyword match, the full extracted
text stays addressable: `outline` lists every section (id, title, size, pages, tables, figures) and
`read` returns the chosen sections / pages / matching passages verbatim, within a character budget.
Both work on the stored Markdown and call no model.
"""
from __future__ import annotations

import re
from typing import Any

from .fit import _PAGE_RE, _split_sections, tokens

_HEADING_LINE = re.compile(r"(?m)^(#{1,6}) ")
_TABLE_SEP = re.compile(r"(?m)^\|\s*:?-{3,}")


def _sections(md: str) -> list[dict[str, Any]]:
    """Sections in document order with a stable integer id, the pages they cover and a few counts.
    Deterministic for a given text, so an id from `outline` always means the same section in `read`."""
    out: list[dict[str, Any]] = []
    page: int | None = None
    for i, (title, text) in enumerate(_split_sections(md or "")):
        pages = [int(p) for p in _PAGE_RE.findall(text)]
        first = page if page is not None else (pages[0] if pages else None)
        if title.startswith("page "):
            first = int(title.split()[1])
        if pages:
            page = pages[-1]
        elif title.startswith("page "):
            page = first
        m = _HEADING_LINE.match(text)
        out.append({
            "id": i, "title": title or "(untitled)", "level": len(m.group(1)) if m else 0,
            "bare": not any(ln.strip() and not _PAGE_RE.match(ln) for ln in text.split("\n")),
            "chars": len(text), "pageFrom": first, "pageTo": page if page is not None else first,
            "tables": len(_TABLE_SEP.findall(text)), "figures": text.count("> **Figure"), "text": text,
        })
    return out


def outline(md: str, *, max_entries: int = 400) -> dict[str, Any]:
    """The table of contents of the document: what exists and how big each part is."""
    secs = _sections(md)
    # a lone page marker is not a part of the document worth listing (ids stay stable for `read`)
    entries = [{k: v for k, v in s.items() if k not in ("text", "bare")} for s in secs if not s["bare"]]
    return {"chars": len(md or ""), "sections": len(entries), "truncated": len(entries) > max_entries,
            "entries": entries[:max_entries]}


def outline_text(md: str, *, max_chars: int = 12_000) -> str:
    """The outline as compact lines, for a model to choose from: `[id] title (chars, p.N-M, tables, figures)`."""
    lines: list[str] = []
    used = 0
    for e in outline(md, max_entries=10_000)["entries"]:
        bits = [f"{e['chars']:,} chars"]
        if e["pageFrom"]:
            bits.append(f"p.{e['pageFrom']}" + (f"-{e['pageTo']}" if e["pageTo"] and e["pageTo"] != e["pageFrom"] else ""))
        if e["tables"]:
            bits.append(f"{e['tables']} table(s)")
        if e["figures"]:
            bits.append(f"{e['figures']} figure(s)")
        line = f"[{e['id']}] {'  ' * max(0, e['level'] - 1)}{e['title'][:90]} ({', '.join(bits)})"
        if used + len(line) > max_chars:
            lines.append("[… outline cut; read by page or search for the rest …]")
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def read(md: str, *, sections: list[int] | None = None, pages: tuple[int, int] | None = None,
         search: str = "", max_chars: int = 20_000) -> dict[str, Any]:
    """Verbatim text of the requested parts, in document order, within `max_chars`.

    sections  ids from `outline`
    pages     inclusive (first, last) page range, for documents that carry page markers
    search    passages (sections) that best match these words, best first
    The result says what was returned and what did not fit (`next`), so the caller can ask again."""
    secs = _sections(md)
    chosen: list[dict[str, Any]] = []
    if sections:
        wanted = set(sections)
        chosen = [s for s in secs if s["id"] in wanted]
    if pages:
        lo, hi = pages
        chosen += [s for s in secs if s["pageFrom"] is not None and s not in chosen
                   and s["pageFrom"] <= hi and (s["pageTo"] or s["pageFrom"]) >= lo]
    if search.strip():
        q = tokens(search)
        scored = sorted(((len(q & tokens(f"{s['title']} {s['text']}")), s) for s in secs), key=lambda x: (-x[0], x[1]["id"]))
        chosen += [s for n, s in scored[:6] if n and s not in chosen]
    chosen.sort(key=lambda s: s["id"])

    out: list[str] = []
    used = 0
    served: list[int] = []
    leftover: list[int] = []
    for s in chosen:
        text = s["text"]
        if used and used + len(text) > max_chars:
            leftover.append(s["id"])
            continue
        if len(text) > max_chars:                       # one huge section: give the start, say so
            text = text[:max_chars].rsplit("\n", 1)[0] + "\n[… section continues; narrow by page or search …]"
            leftover.append(s["id"])
        out.append(text)
        used += len(text)
        served.append(s["id"])
    return {"text": "\n\n".join(out), "sections": served, "next": leftover,
            "found": len(chosen), "chars": used}
