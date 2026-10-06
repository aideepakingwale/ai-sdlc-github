"""One-line human summaries of what document analysis found."""
from __future__ import annotations

from typing import Any


def summarise(stats: dict[str, Any] | None) -> str:
    """e.g. '42 pages · 6 tables · 9 of 11 figures read'."""
    s = stats or {}
    bits: list[str] = []
    for key, one, many in (("pages", "page", "pages"), ("slides", "slide", "slides"), ("sheets", "sheet", "sheets"),
                           ("diagrams", "diagram", "diagrams")):
        n = int(s.get(key, 0) or 0)
        if n:
            bits.append(f"{n} {one if n == 1 else many}")
    for key, one, many in (("tables", "table", "tables"), ("charts", "chart", "charts"),
                           ("diagram_edges", "diagram connection", "diagram connections")):
        n = int(s.get(key, 0) or 0)
        if n:
            bits.append(f"{n} {one if n == 1 else many}")
    found, read = int(s.get("figures_found", 0) or 0), int(s.get("figures_described", 0) or 0)
    if found:
        bits.append(f"{read} of {found} figure{'s' if found != 1 else ''} read")
    return " · ".join(bits)
