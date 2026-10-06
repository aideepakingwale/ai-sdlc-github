"""Shared types for rich document ingestion.

Every parser turns a file into a :class:`RichDocument`: Markdown text in reading
order, plus the *figures* (embedded pictures, rendered diagram pages, slides)
that text alone cannot carry. A figure is referenced from the Markdown by a
marker so that, once a vision model (or OCR) has described it, the description
lands exactly where the figure sat in the document.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

FIG_OPEN, FIG_CLOSE = "⟦FIG:", "⟧"          # ⟦FIG:12⟧ - cannot occur in normal prose
FIG_RE = re.compile(re.escape(FIG_OPEN) + r"(\d+)" + re.escape(FIG_CLOSE))


def marker(figure_id: int) -> str:
    return f"{FIG_OPEN}{figure_id}{FIG_CLOSE}"


class IngestError(Exception):
    """The file cannot be processed (corrupt, unsafe, unsupported). The message is
    safe to show to the user."""


@dataclass
class Limits:
    """Resource bounds for one document. Built from Settings; the defaults keep the
    module usable (and testable) on its own."""
    max_pages: int = 200                 # PDF pages / slides / sheets-rows are capped past this
    max_figures: int = 24                # figures sent to vision per document
    max_rows_per_sheet: int = 1000
    max_chars: int = 300_000             # extracted Markdown kept per document
    max_seconds: float = 90.0            # wall-clock for parsing
    figure_seconds: float = 120.0        # wall-clock for describing figures
    figure_concurrency: int = 3
    figure_max_edge: int = 1568          # longest side sent to the vision model
    min_figure_edge: int = 48            # smaller pictures are icons/bullets - skipped
    min_figure_area: int = 6000
    min_figure_bytes: int = 1500
    render_pages: bool = True            # render slides/pages via LibreOffice when installed
    max_unzipped_bytes: int = 400_000_000


@dataclass
class Figure:
    id: int
    data: bytes
    mime: str
    label: str                           # "page 3", "slide 5", "figure 2"
    caption: str = ""                    # alt text / caption carried by the source
    kind: str = "image"                  # image | page | slide
    width: int = 0
    height: int = 0
    # the text around it in the source - handed to the vision model as context
    context: str = ""

    @property
    def area(self) -> int:
        return self.width * self.height


@dataclass
class RichDocument:
    kind: str                            # document | text | image | diagram | binary
    markdown: str = ""
    figures: list[Figure] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def new_figure(self, data: bytes, mime: str, label: str, **kw: Any) -> Figure:
        fig = Figure(id=len(self.figures) + 1, data=data, mime=mime, label=label, **kw)
        self.figures.append(fig)
        return fig

    def bump(self, key: str, n: int = 1) -> None:
        self.stats[key] = int(self.stats.get(key, 0)) + n

    def outline(self, limit: int = 60) -> list[str]:
        """Heading lines of the Markdown (for the plan digest and for budget fitting)."""
        heads = [m.group(0).strip() for m in re.finditer(r"(?m)^#{1,4} .+$", self.markdown)]
        return heads[:limit]


def tidy(md: str) -> str:
    """Collapse the excess blank lines converters leave behind."""
    return re.sub(r"\n{3,}", "\n\n", (md or "")).strip()


def md_table(rows: list[list[str]]) -> str:
    """Render rows (first row = header) as a GitHub-flavoured Markdown table."""
    rows = [[(c or "").replace("\n", " ").replace("|", "\\|").strip() for c in r] for r in rows]
    rows = [r for r in rows if any(r)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)
