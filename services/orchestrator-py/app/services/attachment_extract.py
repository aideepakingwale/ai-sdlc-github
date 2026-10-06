"""Extract usable context text from uploaded attachments (D-65, D-66, D-112).

Users attach *documents*, not just text: requirements as PDF / Word, decks with
diagrams, spreadsheets, screenshots, draw.io files. This turns every supported
file into structured **Markdown** the context pipeline can inline into a prompt:

  text/*            decoded as-is
  PDF               layout-aware text, real tables, inferred headings; pages that
                    carry diagrams or are scans are rendered and read by vision
  DOCX              headings / lists / tables, embedded pictures read in place,
                    text boxes, shape diagrams rendered when LibreOffice exists
  PPTX              per slide: text, tables, chart data, SmartArt, grouped shapes,
                    speaker notes, diagram connections ("A -> B"), pictures and
                    (with LibreOffice) the rendered slide for the vision model
  XLSX              one table per sheet
  draw.io / Visio / SVG   components and directed connections, from the XML
  images            vision model (diagram-aware) with OCR as the offline fallback
  legacy .doc/.ppt/.xls/.rtf/.odt  converted through LibreOffice when installed

The work lives in :mod:`app.services.documents`; this module is the stable
façade the API and the older call sites use. Every parser is guarded: a missing
library, renderer or vision provider degrades to a clear note rather than
failing the upload.
"""

from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass, field
from typing import Any

from .documents import IngestError, Limits, RichDocument, ingest
from .documents.ingest import IMAGE_EXTS, parse
from .documents.model import Figure

log = logging.getLogger("attach")

# How the attachment was interpreted — surfaced to the UI/audit.
KIND_TEXT = "text"
KIND_DOCUMENT = "document"
KIND_IMAGE = "image"
KIND_BINARY = "binary"

_MAX_CHARS = 300_000  # cap extracted text so one attachment can't blow the context
# The vision instructions live in the central prompt library (D-48): templates
# `attachment.vision.user` (a standalone image) and `attachment.vision.figure.user`
# (a figure taken from a document).


def _looks_like(filename: str, content_type: str, *exts: str, mime_prefix: str | None = None) -> bool:
    name = filename.lower()
    if any(name.endswith(e) for e in exts):
        return True
    return bool(mime_prefix and content_type.lower().startswith(mime_prefix))


def is_image(filename: str, content_type: str) -> bool:
    return _looks_like(filename, content_type, *IMAGE_EXTS, mime_prefix="image/")


def limits_from(settings: Any | None = None) -> Limits:
    """Resource bounds for document analysis, from Settings (defaults when absent)."""
    g = lambda name, default: getattr(settings, name, default)  # noqa: E731
    return Limits(
        max_pages=g("ATTACHMENT_MAX_PAGES", 200),
        max_figures=g("ATTACHMENT_MAX_FIGURES", 24),
        max_chars=g("ATTACHMENT_STORE_CHARS", _MAX_CHARS),
        max_seconds=float(g("ATTACHMENT_PARSE_SECONDS", 90)),
        figure_seconds=float(g("ATTACHMENT_ANALYSIS_SECONDS", 120)),
        figure_concurrency=g("ATTACHMENT_FIGURE_CONCURRENCY", 3),
        figure_max_edge=g("ATTACHMENT_VISION_MAX_EDGE", 1568),
        render_pages=g("ATTACHMENT_RENDER_PAGES", "auto") != "off",
    )


@dataclass
class Extracted:
    """The result of analysing one attachment."""
    text: str
    kind: str
    note: str = ""
    method: str = "parse"
    stats: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    outline: list[str] = field(default_factory=list)


def _kind_of(doc: RichDocument) -> str:
    return {"text": KIND_TEXT, "image": KIND_IMAGE, "binary": KIND_BINARY}.get(doc.kind, KIND_DOCUMENT)


def _method_of(doc: RichDocument, vision: set[str]) -> str:
    if vision:
        return ",".join(sorted(vision))
    return "ocr" if doc.kind == "image" else "parse"


def _result(doc: RichDocument, *, empty_note: str = "") -> Extracted:
    text = doc.markdown.strip()
    warnings = list(doc.warnings)
    if not text and not warnings:
        warnings.append(empty_note or "no text could be extracted")
    methods = {w for w in doc.stats.pop("_methods", [])}
    return Extracted(text=text, kind=_kind_of(doc), note="; ".join(warnings),
                     method=_method_of(doc, methods), stats=doc.stats, warnings=warnings,
                     outline=doc.outline(40))


# ------------------------------------------------------------------ OCR (offline fallback)
def _extract_image_ocr(raw: bytes) -> str:
    import pytesseract
    from PIL import Image

    img = Image.open(io.BytesIO(raw))
    return (pytesseract.image_to_string(img) or "").strip()


def _ocr_describer():
    async def describe(fig: Figure) -> tuple[str, str] | None:
        import asyncio
        try:
            txt = await asyncio.to_thread(_extract_image_ocr, fig.data)
        except Exception:  # noqa: BLE001 - OCR engine missing / unreadable image
            return None
        return (f"Text in the image (OCR):\n{txt}", "ocr") if txt else None
    return describe


def extract(raw: bytes, filename: str, content_type: str) -> tuple[str, str, str]:
    """Synchronous extraction, no vision model: (text, kind, note). Documents are
    parsed (tables, headings, diagram structure); figures are read with OCR when it
    is available and otherwise noted as not analysed. `text` is inlineable context
    ('' if none); `note` explains a degraded result (empty on success)."""
    from .documents.figures import FigureResult, apply_descriptions

    filename = filename or "attachment"
    content_type = content_type or "application/octet-stream"
    limits = limits_from(None)
    kind_hint = KIND_IMAGE if is_image(filename, content_type) else KIND_DOCUMENT
    try:
        doc = parse(raw, filename, content_type, limits)
    except IngestError as err:
        return "", kind_hint, str(err)
    except Exception as err:  # noqa: BLE001
        log.warning("extract failed for %s: %s", filename, err)
        return "", kind_hint, f"could not process the file ({err})"
    if doc.figures:
        results: dict[int, FigureResult] = {}
        for fig in doc.figures[: limits.max_figures]:
            try:
                txt = _extract_image_ocr(fig.data)
            except Exception:  # noqa: BLE001 - OCR engine missing / unreadable image
                txt = ""
            results[fig.id] = (FigureResult(fig.id, fig.label, "described", f"Text in the image (OCR):\n{txt}", "ocr")
                               if txt else FigureResult(fig.id, fig.label, "failed", reason="OCR found no text"))
        for fig in doc.figures[limits.max_figures:]:
            results[fig.id] = FigureResult(fig.id, fig.label, "skipped", reason="figure limit reached")
        apply_descriptions(doc, results)
    res = _result(doc)
    if doc.kind == "image" and not res.text:
        res.note = "no text detected in the image (OCR found nothing)"
    return res.text, res.kind, res.note


async def extract_rich(raw: bytes, filename: str, content_type: str, *, llm: Any = None,
                       settings: Any = None) -> Extracted:
    """Full analysis: parse the document, describe its figures with the vision model
    (OCR fallback), merge the descriptions in place. Never raises for a file it can
    partly read; raises :class:`IngestError` only for a file that is unusable."""
    filename = filename or "attachment"
    content_type = content_type or "application/octet-stream"
    limits = limits_from(settings)
    mode = getattr(settings, "ATTACHMENT_VISION", "auto")
    used: set[str] = set()
    describer = _make_describer(llm, mode, limits, filename, used)
    doc = await ingest(raw, filename, content_type, limits=limits, describer=describer)
    doc.stats["_methods"] = list(used)
    res = _result(doc, empty_note="the document had no readable text")
    res.stats.pop("_methods", None)
    return res


def _make_describer(llm: Any, mode: str, limits: Limits, filename: str, used: set[str]):
    ocr = _ocr_describer()

    async def describe(fig: Figure) -> tuple[str, str] | None:
        if mode != "ocr" and llm is not None:
            standalone = fig.label == "image"
            vis = await describe_image_llm(
                fig.data, fig.mime, llm=llm, max_edge=limits.figure_max_edge,
                tag="attachment.vision" if standalone else "attachment.figure",
                prompt_id="attachment.vision.user" if standalone else "attachment.vision.figure.user",
                prompt_vars=None if standalone else {
                    "document": filename, "location": fig.label,
                    "caption": fig.caption or "none", "surrounding": (fig.context or "none")[:300]},
            )
            if vis is not None:
                text, provider = vis
                used.add(f"vision:{provider}")
                if standalone:                       # keep the exact words alongside the description
                    exact = await ocr(fig)
                    if exact:
                        text = f"{text}\n\n---\nOCR (verbatim, deterministic):\n{exact[0].split(chr(10), 1)[-1]}"
                return text, f"vision:{provider}"
        got = await ocr(fig)
        if got:
            used.add("ocr")
        return got

    return describe


def _downscale_image(raw: bytes, max_edge: int) -> tuple[bytes, str]:
    """Re-encode an image down to `max_edge` on its longest side as JPEG, to cap
    the vision request payload and token cost. Returns (bytes, mime). Falls back
    to the original bytes (PNG) if anything goes wrong."""
    from PIL import Image

    img = Image.open(io.BytesIO(raw))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > max_edge:
        scale = max_edge / float(max(w, h))
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue(), "image/jpeg"


async def describe_image_llm(
    raw: bytes, content_type: str, *, llm: Any, max_edge: int = 1536, tag: str | None = None,
    prompt_id: str = "attachment.vision.user", prompt_vars: dict[str, str] | None = None,
) -> tuple[str, str] | None:
    """Vision extraction via the ai-client gateway (multi-model: Bedrock → Gemini,
    D-66). Returns (text, provider) on success, or None when no *real* vision
    provider served it (the deterministic mock is treated as "not available", so
    the caller falls back to OCR). Never raises — any failure returns None."""
    try:
        img_bytes, mime = _downscale_image(raw, max_edge)
    except Exception as err:  # noqa: BLE001 — bad/unsupported image
        log.warning("vision downscale failed: %s", err)
        img_bytes, mime = raw, (content_type or "image/png")

    from .prompt_library import render as render_prompt

    b64 = base64.b64encode(img_bytes).decode("ascii")
    messages = [
        {"role": "user", "content": render_prompt(prompt_id, **(prompt_vars or {})),
         "images": [{"mimeType": mime, "dataBase64": b64}]},
    ]
    try:
        # 'recommendation' intent → vision-filtered chain leads with Bedrock, then Gemini.
        result = await llm.generate(
            intent="recommendation", messages=messages,
            temperature=0.1, max_tokens=4096, tag=tag or "attachment.vision", role="vision",
        )
    except Exception as err:  # noqa: BLE001 — provider exhausted / gateway down
        log.warning("vision LLM call failed: %s", err)
        return None

    if result.provider == "mock" or not (result.content or "").strip():
        return None  # no real vision provider available → let OCR handle it
    return result.content.strip()[:_MAX_CHARS], result.provider


__all__ = ["Extracted", "IngestError", "KIND_BINARY", "KIND_DOCUMENT", "KIND_IMAGE", "KIND_TEXT",
           "describe_image_llm", "extract", "extract_rich", "is_image", "limits_from", "parse"]
