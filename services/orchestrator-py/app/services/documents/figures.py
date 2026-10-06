"""Describe the figures a document carries and put the descriptions back in place.

A figure is a picture, a rendered diagram page or a rendered slide. Each unique
figure (identical images are described once) goes to a vision model - a few at a
time, largest and most informative first, within a wall-clock budget - and the
description replaces the figure's marker in the Markdown, so a diagram's meaning
sits exactly where the diagram sat. Anything that cannot be described is reported
explicitly instead of being silently dropped.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .model import FIG_RE, Figure, Limits, RichDocument, tidy

log = logging.getLogger("attach.figures")

# (figure) -> (description, method) | None. `method` is e.g. "vision:bedrock" or "ocr".
Describer = Callable[[Figure], Awaitable[tuple[str, str] | None]]


@dataclass
class FigureResult:
    figure_id: int
    label: str
    status: str                  # described | skipped | failed
    text: str = ""
    method: str = ""
    reason: str = ""


def _priority(fig: Figure) -> tuple[int, int]:
    # whole slides / pages first (they carry the most meaning), then biggest pictures
    return (0 if fig.kind in ("slide", "page") else 1, -fig.area)


async def describe_figures(doc: RichDocument, describer: Describer, limits: Limits) -> dict[int, FigureResult]:
    results: dict[int, FigureResult] = {}
    ordered = sorted(doc.figures, key=_priority)
    by_hash: dict[str, Figure] = {}
    work: list[Figure] = []
    duplicates: dict[int, int] = {}
    for fig in ordered:
        digest = hashlib.sha256(fig.data).hexdigest()
        if digest in by_hash:
            duplicates[fig.id] = by_hash[digest].id
            continue
        by_hash[digest] = fig
        if len(work) < limits.max_figures:
            work.append(fig)
        else:
            results[fig.id] = FigureResult(fig.id, fig.label, "skipped",
                                           reason=f"figure limit reached ({limits.max_figures} per document)")

    deadline = time.monotonic() + limits.figure_seconds
    sem = asyncio.Semaphore(max(1, limits.figure_concurrency))

    async def one(fig: Figure) -> None:
        async with sem:
            remaining = deadline - time.monotonic()
            if remaining <= 1:
                results[fig.id] = FigureResult(fig.id, fig.label, "skipped", reason="analysis time budget reached")
                return
            try:
                got = await asyncio.wait_for(describer(fig), timeout=remaining)
            except asyncio.TimeoutError:
                results[fig.id] = FigureResult(fig.id, fig.label, "skipped", reason="analysis time budget reached")
                return
            except Exception as err:  # noqa: BLE001 - a bad figure must never fail the upload
                log.warning("figure %s (%s) failed: %s", fig.id, fig.label, err)
                got = None
            if got and got[0].strip():
                results[fig.id] = FigureResult(fig.id, fig.label, "described", got[0].strip(), got[1])
            else:
                results[fig.id] = FigureResult(fig.id, fig.label, "failed",
                                               reason="no vision model or OCR was available to read it")

    await asyncio.gather(*(one(f) for f in work))
    for dup_id, src_id in duplicates.items():
        src = results.get(src_id)
        dup = next(f for f in doc.figures if f.id == dup_id)
        results[dup_id] = (FigureResult(dup_id, dup.label, src.status, src.text, src.method, src.reason)
                           if src else FigureResult(dup_id, dup.label, "skipped", reason="duplicate"))
    return results


def apply_descriptions(doc: RichDocument, results: dict[int, FigureResult]) -> None:
    """Replace each figure marker with its description (or an honest note)."""
    figs = {f.id: f for f in doc.figures}

    def render(m) -> str:  # noqa: ANN001
        fid = int(m.group(1))
        fig, res = figs.get(fid), results.get(fid)
        if fig is None or res is None:
            return ""
        title = f"Figure — {fig.label}" + (f" — {fig.caption}" if fig.caption else "")
        if res.status == "described":
            body = "\n".join(f"> {ln}" if ln.strip() else ">" for ln in res.text.splitlines())
            return f"\n\n> **{title}** · _read by {res.method}_\n{body}\n\n"
        return f"\n\n> _[{title}: not analysed — {res.reason}]_\n\n"

    doc.markdown = tidy(FIG_RE.sub(render, doc.markdown))
    described = sum(1 for r in results.values() if r.status == "described")
    doc.stats["figures_found"] = len(doc.figures)
    doc.stats["figures_described"] = described
    unread = [r for r in results.values() if r.status != "described"]
    if unread:
        doc.stats["figures_unread"] = len(unread)
        doc.warnings.append(f"{len(unread)} figure(s) could not be analysed ({unread[0].reason})")


def strip_markers(doc: RichDocument) -> None:
    """No describer at all (sync/offline path): remove markers, keep an honest note."""
    figs = {f.id: f for f in doc.figures}

    def note(m) -> str:  # noqa: ANN001
        fig = figs.get(int(m.group(1)))
        return f"\n\n> _[Figure — {fig.label}: not analysed]_\n\n" if fig else ""

    doc.markdown = tidy(FIG_RE.sub(note, doc.markdown))
    doc.stats["figures_found"] = len(doc.figures)
    doc.stats["figures_described"] = 0
