"""Fit documents into a prompt budget without losing parts of them.

The old behaviour cut every attachment at a fixed character count, so everything
after the first few pages silently vanished. Fitting works on *sections* instead:

1. Every section is guaranteed its heading and the opening of its text, so the
   whole document stays represented (a document outline is added when it helps).
2. The remaining budget goes to the sections that best match what the stage is
   being asked to produce (term overlap), tables and figure descriptions
   preferred, in original order.
3. Omitted material is marked, never hidden.

Several documents share the total budget fairly (small ones keep everything and
free up room for the large ones).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING_RE = re.compile(r"(?m)^(#{1,4}) (.+)$")
_PAGE_RE = re.compile(r"(?m)^<!-- page (\d+) -->$")
_WORD_RE = re.compile(r"[a-z0-9]{3,}")
_STOP = frozenset("""the and for with that this from will shall must should have has are was were not but can
all any per via into over under such than then them they their there which when where while what who whom
generate create produce write build make need needs please based using use used also each both more most
document documents attached attachment file files stage output outputs""".split())


def tokens(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall((text or "").lower()) if w not in _STOP}


@dataclass
class _Chunk:
    idx: int
    section: int
    title: str
    text: str
    first_in_section: bool
    toks: set[str]
    take: int = 0                      # characters of `text` that will be emitted

    @property
    def full(self) -> bool:
        return self.take >= len(self.text)


def _split_sections(md: str) -> list[tuple[str, str]]:
    """[(title, text)] - split at headings and page markers (not inside code fences)."""
    sections: list[tuple[str, list[str]]] = [("", [])]
    in_fence = False
    for line in md.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        h = None if in_fence else _HEADING_RE.match(line)
        pg = None if in_fence else _PAGE_RE.match(line)
        if h:
            sections.append((h.group(2).strip(), [line]))
        elif pg and sections[-1][1] and any(ln.strip() and not _PAGE_RE.match(ln) for ln in sections[-1][1]):
            sections.append((f"page {pg.group(1)}", [line]))
        else:
            sections[-1][1].append(line)
    return [(t, "\n".join(lines).strip()) for t, lines in sections if "\n".join(lines).strip()]


def _pieces(text: str, size: int) -> list[str]:
    """Paragraphs, with any paragraph longer than `size` split at sentence boundaries."""
    out: list[str] = []
    for para in re.split(r"\n{2,}", text):
        if len(para) <= size * 1.3:
            out.append(para)
            continue
        cur = ""
        for sent in re.split(r"(?<=[.!?])\s+|\n", para):
            if cur and len(cur) + len(sent) + 1 > size:
                out.append(cur)
                cur = sent
            else:
                cur = f"{cur} {sent}" if cur else sent
        if cur:
            out.append(cur)
    return out


def _chunks(sections: list[tuple[str, str]], size: int = 2200) -> list[_Chunk]:
    out: list[_Chunk] = []
    for si, (title, text) in enumerate(sections):
        if len(text) <= size * 1.3:
            parts = [text]
        else:
            parts, cur = [], ""
            for para in _pieces(text, size):
                heading_only = cur.lstrip().startswith("#") and "\n" not in cur.strip()
                if cur and not heading_only and len(cur) + len(para) > size:
                    parts.append(cur)
                    cur = para
                else:
                    cur = f"{cur}\n\n{para}" if cur else para     # a heading always travels with its first paragraph
            if cur:
                parts.append(cur)
        for pi, part in enumerate(parts):
            out.append(_Chunk(len(out), si, title, part, pi == 0, tokens(f"{title} {part}")))
    return out


def _cut(text: str, n: int) -> str:
    """First ~n characters, ending at a paragraph / line / sentence boundary (never mid-table-row)."""
    if n >= len(text):
        return text
    window = text[:n]
    for sep in ("\n\n", "\n", ". "):
        k = window.rfind(sep)
        if k >= n * 0.5:
            return window[: k + len(sep)].rstrip()
    return window.rstrip()


def fit_document(md: str, budget: int, query: str = "", pinned: set[int] | None = None) -> str:
    """Return `md` trimmed to at most `budget` characters - see the module docstring.
    `pinned` are section ids (see documents.reader) that must be kept in full before anything else is."""
    md = (md or "").strip()
    if budget <= 0 or len(md) <= budget:
        return md
    sections = _split_sections(md)
    chunks = _chunks(sections)
    if not chunks:
        return md[:budget]

    headings = [t for t, _ in sections if t and not t.startswith("page ")]
    outline = ""
    if len(headings) >= 4:
        listed = "; ".join(h[:70] for h in headings[:40])
        outline = f"[Document outline: {listed[: max(200, int(budget * 0.05))]}]\n\n"
    q = tokens(query)

    def score(c: _Chunk) -> float:
        overlap = len(q & c.toks) / (len(q) ** 0.5 or 1) if q else 0.0
        bonus = (0.4 if "| ---" in c.text else 0.0) + (0.3 if "> **Figure" in c.text else 0.0)
        pin = 100.0 if pinned and c.section in pinned else 0.0
        return pin + overlap + bonus + (0.25 if c.idx == 0 else 0.0) - c.idx * 1e-6

    ranked = sorted(chunks, key=score, reverse=True)
    n_sections = max(1, len(sections))

    def build(room: int) -> str:
        for c in chunks:
            c.take = 0
        # 1) coverage: every section keeps its opening (as many as the budget allows)
        head = max(40, min(900, int(room * 0.6 / n_sections)))
        used = 0
        for c in chunks:
            if c.first_in_section:
                first_line = len(c.text.split("\n", 1)[0])
                take = len(_cut(c.text, max(head, first_line + 45)))      # heading + the start of its text
                if used + take > room:
                    break
                c.take, used = take, used + take
        # 2) relevance: spend what is left on the best-matching chunks (never all of it
        #    on one section while others have only their heading)
        for c in ranked:
            if used >= room:
                break
            need = len(c.text) - c.take
            if need <= 0:
                continue
            grant = min(need, room - used)
            if grant < need and grant < 200:
                continue
            c.take = len(c.text) if grant >= need else (c.take + len(_cut(c.text[c.take:], grant)) if c.take
                                                         else len(_cut(c.text, grant)))
            used += grant
        # 3) emit by section in original order, marking what was left out
        out: list[str] = []
        gap: list[str] = []

        def flush_gap() -> None:
            if gap:
                names = ", ".join(dict.fromkeys(g for g in gap if g))[:160]
                out.append(f"[… omitted to fit the prompt budget{': ' + names if names else ''} …]")
                gap.clear()

        for si in range(len(sections)):
            mine = [c for c in chunks if c.section == si]
            if not any(c.take > 0 for c in mine):
                gap.append(mine[0].title if mine else "")
                continue
            flush_gap()
            text = "\n\n".join(c.text[: c.take].rstrip() for c in mine if c.take > 0)
            if not all(c.full for c in mine):
                text += "\n[… rest of this section omitted …]"
            out.append(text)
        flush_gap()
        return "\n\n".join(out)

    # Headings and omission markers cost characters too: reserve them before spending.
    overhead = sum(len(title) + 55 for title, _ in sections)
    room = max(budget // 4, budget - len(outline) - overhead)
    result = outline + build(room)
    for _ in range(6):                      # markers and headings cost characters: tighten until it fits
        over = len(result) - budget
        if over <= 0:
            break
        room -= over + 16
        if room <= 0:
            break
        result = outline + build(room)
    return result if len(result) <= budget else result[:budget]


def allocate(sizes: list[int], total: int) -> list[int]:
    """Fair-share a character budget: small documents keep all they need, the rest is
    split evenly among the larger ones."""
    n = len(sizes)
    alloc = [0] * n
    remaining = total
    order = sorted(range(n), key=lambda i: sizes[i])
    for pos, i in enumerate(order):
        share = remaining // (n - pos)
        alloc[i] = min(sizes[i], share)
        remaining -= alloc[i]
    return alloc
