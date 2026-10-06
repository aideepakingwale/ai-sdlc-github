"""PDF -> Markdown with tables, headings, and the pages that need to be *seen*.

pypdf's flat text dump loses tables, headings and everything drawn rather than
typed. This parser uses pdfplumber for layout-aware text and real tables, infers
headings from font size, drops running headers/footers, and flags the pages that
carry diagrams or are scans so they can be rendered (pdfium) and described by a
vision model. Pure vector diagrams - which are not images at all - are caught by
counting drawn paths outside tables.
"""
from __future__ import annotations

import io
import logging
import re
import time
from collections import Counter
from typing import Any

from .model import IngestError, Limits, RichDocument, marker, md_table, tidy

log = logging.getLogger("attach.pdf")

_BULLET_RE = re.compile(r"^\s*([•●▪◦‣\-–—*]|\d{1,3}[.)])\s+")
_GRAPHIC_THRESHOLD = 8           # drawn paths outside tables that make a page "a diagram"
_SCANNED_TEXT_CHARS = 40         # fewer typed characters than this + an image = a scan


def _inside(obj: dict[str, Any], boxes: list[tuple[float, float, float, float]]) -> bool:
    cx = (obj["x0"] + obj["x1"]) / 2
    cy = (obj["top"] + obj["bottom"]) / 2
    return any(b[0] - 1 <= cx <= b[2] + 1 and b[1] - 1 <= cy <= b[3] + 1 for b in boxes)


def _body_size(pdf: Any) -> float:
    sizes: Counter[float] = Counter()
    for page in pdf.pages[:25]:
        for ch in page.chars:
            sizes[round(float(ch.get("size", 0)), 1)] += 1
    return sizes.most_common(1)[0][0] if sizes else 10.0


def _heading_level(text: str, size: float, bold: bool, body: float) -> int:
    if not text or len(text) > 120 or text.rstrip().endswith((".", ",", ";", ":")):
        return 0
    ratio = size / body if body else 1.0
    if ratio >= 1.6:
        return 1
    if ratio >= 1.3:
        return 2
    if ratio >= 1.12 or (bold and ratio >= 1.0 and len(text) <= 80 and text[:1].isupper()):
        return 3
    return 0


def _is_bold(chars: list[dict[str, Any]]) -> bool:
    bold = sum(1 for c in chars if "bold" in str(c.get("fontname", "")).lower())
    return bool(chars) and bold / len(chars) > 0.6


def _norm(text: str) -> str:
    return re.sub(r"\d+", "#", text.strip().lower())


class _Item:
    __slots__ = ("kind", "top", "bottom", "text", "size", "bold", "page")

    def __init__(self, kind: str, top: float, bottom: float, text: str, size: float = 0.0,
                 bold: bool = False, page: int = 0) -> None:
        self.kind, self.top, self.bottom, self.text = kind, top, bottom, text
        self.size, self.bold, self.page = size, bold, page


def parse_pdf(raw: bytes, limits: Limits) -> RichDocument:
    try:
        import pdfplumber
    except ImportError as err:  # pragma: no cover - dependency is declared
        raise IngestError("PDF support is not installed on this server") from err

    started = time.monotonic()
    doc = RichDocument(kind="document")
    try:
        pdf = pdfplumber.open(io.BytesIO(raw))
    except Exception as err:  # noqa: BLE001 - corrupt / encrypted
        raise IngestError(f"could not open the PDF ({type(err).__name__}); it may be corrupt or password-protected") from err

    with pdf:
        total_pages = len(pdf.pages)
        doc.stats["pages"] = total_pages
        body = _body_size(pdf)
        pages: list[list[_Item]] = []
        page_info: dict[int, dict[str, Any]] = {}
        processed = 0
        for pno, page in enumerate(pdf.pages[: limits.max_pages], start=1):
            if time.monotonic() - started > limits.max_seconds:
                doc.warnings.append(f"time budget reached: read pages 1-{pno - 1} of {total_pages}")
                break
            try:
                items, info = _read_page(page, pno, body)
            except Exception as err:  # noqa: BLE001 - one bad page must not lose the document
                log.warning("pdf page %s failed: %s", pno, err)
                doc.warnings.append(f"page {pno} could not be read")
                items, info = [], {"graphics": 0, "image_area": 0.0, "text_chars": 0, "images": 0}
            pages.append(items)
            page_info[pno] = info
            processed = pno
            doc.bump("tables", info.get("tables", 0))
        if processed < total_pages and not any("time budget" in w for w in doc.warnings):
            doc.warnings.append(f"only the first {processed} of {total_pages} pages were read (page limit)")
        page_height = float(pdf.pages[0].height) if pdf.pages else 842.0

    _drop_running_headers(pages, page_height, body)
    candidates = _render_candidates(page_info)
    chosen = candidates[: limits.max_figures]
    skipped = [p for p, _ in candidates[limits.max_figures:]]
    figure_pages = _render_pages(raw, [p for p, _ in chosen], limits, doc, page_info)

    out: list[str] = []
    for pno, items in enumerate(pages, start=1):
        out.append(f"<!-- page {pno} -->")
        out.append(_assemble(items, body))
        fid = figure_pages.get(pno)
        if fid:
            out.append(marker(fid))
    if skipped:
        doc.warnings.append(
            f"{len(skipped)} page(s) with diagrams or scans were not analysed (figure limit {limits.max_figures}): "
            + ", ".join(str(p) for p in skipped[:12]) + ("…" if len(skipped) > 12 else ""))
    doc.markdown = tidy("\n\n".join(s for s in out if s))
    if not re.sub(r"<!--.*?-->|⟦FIG:\d+⟧", "", doc.markdown).strip() and not doc.figures:
        doc.warnings.append("no extractable text in the PDF (scanned, and page rendering is unavailable)")
    return doc


def _read_page(page: Any, pno: int, body: float) -> tuple[list[_Item], dict[str, Any]]:
    tables = []
    try:
        tables = page.find_tables()
    except Exception:  # noqa: BLE001 - table detection is best-effort
        tables = []
    boxes = [tuple(t.bbox) for t in tables]
    items: list[_Item] = []
    for t in tables:
        try:
            rows = [[(c or "") for c in row] for row in t.extract()]
        except Exception:  # noqa: BLE001
            continue
        md = md_table(rows)
        if md:
            items.append(_Item("table", t.bbox[1], t.bbox[3], md, page=pno))

    view = page.filter(lambda o: o.get("object_type") != "char" or not _inside(o, boxes)) if boxes else page
    text_chars = 0
    for ln in view.extract_text_lines(return_chars=True, strip=True):
        text = (ln.get("text") or "").strip()
        if not text:
            continue
        chars = ln.get("chars") or []
        size = max((float(c.get("size", 0)) for c in chars), default=body)
        items.append(_Item("line", ln["top"], ln["bottom"], text, size, _is_bold(chars), pno))
        text_chars += len(text)

    graphics = [o for kind in ("curves", "lines", "rects") for o in getattr(page, kind, [])
                if not _inside(o, boxes)]
    page_area = float(page.width * page.height) or 1.0
    image_area = sum(max(0.0, (i["x1"] - i["x0"]) * (i["bottom"] - i["top"])) for i in page.images) / page_area
    return items, {"graphics": len(graphics), "image_area": image_area, "text_chars": text_chars,
                   "images": len(page.images), "tables": len(tables)}


def _drop_running_headers(pages: list[list[_Item]], page_height: float, body: float) -> None:
    """Remove lines repeated at the top/bottom of most pages (headers, footers, page numbers)."""
    if len(pages) < 4:
        return
    band = page_height * 0.08
    seen: Counter[str] = Counter()
    for items in pages:
        keys = {_norm(i.text) for i in items
                if i.kind == "line" and i.size <= body * 1.1 and (i.top < band or i.bottom > page_height - band)}
        seen.update(keys)
    repeated = {k for k, n in seen.items() if n >= max(3, int(len(pages) * 0.4))}
    if not repeated:
        return
    for items in pages:
        items[:] = [i for i in items if not (
            i.kind == "line" and i.size <= body * 1.1
            and (i.top < band or i.bottom > page_height - band) and _norm(i.text) in repeated)]


def _assemble(items: list[_Item], body: float) -> str:
    items = sorted(items, key=lambda i: (i.top, i.kind != "table"))
    out: list[str] = []
    para: list[str] = []
    prev: _Item | None = None

    def flush() -> None:
        if para:
            out.append(" ".join(para))
            para.clear()

    for it in items:
        if it.kind == "table":
            flush()
            out.append(it.text)
            prev = it
            continue
        level = _heading_level(it.text, it.size, it.bold, body)
        if level:
            flush()
            out.append(f"{'#' * level} {it.text}")
            prev = it
            continue
        m = _BULLET_RE.match(it.text)
        if m:
            flush()
            marker_txt = m.group(1)
            ordered = marker_txt[0].isdigit()
            out.append(f"{marker_txt if ordered else '-'} {it.text[m.end():].strip()}")
            prev = it
            continue
        gap = (it.top - prev.bottom) if prev is not None else 0.0
        continues = prev is not None and prev.kind == "line" and gap < max(it.size, 1) * 0.7 and para
        if not continues:
            flush()
        if para and para[-1].endswith("-") and not para[-1].endswith(" -"):
            para[-1] = para[-1][:-1] + it.text          # re-join a hyphenated word
        else:
            para.append(it.text)
        prev = it
    flush()
    return "\n\n".join(out)


def _render_candidates(info: dict[int, dict[str, Any]]) -> list[tuple[int, float]]:
    """Pages worth showing to a vision model, most valuable first."""
    scored: list[tuple[int, float]] = []
    for pno, i in info.items():
        scanned = i["text_chars"] < _SCANNED_TEXT_CHARS and (i["images"] > 0 or i["graphics"] > 0)
        diagram = i["graphics"] >= _GRAPHIC_THRESHOLD or i["image_area"] >= 0.03
        if not (scanned or diagram):
            continue
        score = (1000.0 if scanned else 0.0) + i["graphics"] * 2 + i["image_area"] * 400
        scored.append((pno, score))
    scored.sort(key=lambda t: (-t[1], t[0]))
    return scored


def _render_pages(raw: bytes, pnos: list[int], limits: Limits, doc: RichDocument,
                  info: dict[int, dict[str, Any]]) -> dict[int, int]:
    """Render the chosen pages to images; returns {page number: figure id}."""
    if not pnos:
        return {}
    try:
        import pypdfium2 as pdfium
    except ImportError:
        doc.warnings.append("page rendering is unavailable, so diagrams and scanned pages were not analysed")
        return {}
    out: dict[int, int] = {}
    try:
        pdf = pdfium.PdfDocument(raw)
    except Exception as err:  # noqa: BLE001
        doc.warnings.append(f"could not render pages ({type(err).__name__})")
        return {}
    try:
        for pno in sorted(pnos):
            try:
                page = pdf[pno - 1]
                w, h = page.get_size()
                scale = max(0.5, min(2.5, limits.figure_max_edge / max(w, h)))
                img = page.render(scale=scale).to_pil().convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85)
                i = info.get(pno, {})
                what = "scanned page" if i.get("text_chars", 0) < _SCANNED_TEXT_CHARS else "page with diagrams"
                fig = doc.new_figure(buf.getvalue(), "image/jpeg", f"page {pno}", kind="page",
                                     width=img.width, height=img.height, caption=what)
                out[pno] = fig.id
            except Exception as err:  # noqa: BLE001
                log.warning("render page %s failed: %s", pno, err)
    finally:
        pdf.close()
    return out


def render_graphic_pages(pdf_bytes: bytes, limits: Limits, doc: RichDocument, *, max_pages: int) -> list[int]:
    """For a PDF produced from another format (LibreOffice): render the pages that
    carry drawn graphics and return their figure ids. Used for Word documents whose
    diagrams are shapes rather than pictures."""
    try:
        import pdfplumber
    except ImportError:
        return []
    info: dict[int, dict[str, Any]] = {}
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            body = _body_size(pdf)
            for pno, page in enumerate(pdf.pages[: limits.max_pages], start=1):
                _, i = _read_page(page, pno, body)
                info[pno] = i
    except Exception as err:  # noqa: BLE001
        log.warning("could not inspect rendered pages: %s", err)
        return []
    wanted = [(p, s) for p, s in _render_candidates(info) if info[p]["graphics"] >= _GRAPHIC_THRESHOLD][:max_pages]
    pages = _render_pages(pdf_bytes, [p for p, _ in wanted], limits, doc, info)
    return [pages[p] for p in sorted(pages)]
