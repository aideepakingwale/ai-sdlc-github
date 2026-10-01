"""Extract usable context text from uploaded attachments (D-65, D-66).

Users attach not just plain text but **documents** (PDF, Word) and **images**
(screenshots of requirements, photos of a whiteboard, scanned specs). This turns
each into text that the D-54 context pipeline can inline into the agent prompt:

  - text/*             -> decoded as-is
  - PDF                -> extracted page text (pypdf)
  - DOCX               -> clean Markdown (mammoth + markdownify; python-docx fallback)
  - XLSX               -> one Markdown table per sheet (openpyxl, values resolved)
  - PPTX               -> Markdown, each slide a section (python-pptx)
  - image/*            -> vision LLM (D-66, preferred) or OCR (pytesseract)
  - anything else      -> not inlined (kept as a labelled reference)

Office documents are converted to structured **Markdown** rather than a flat text
dump (D-112): this strips the OOXML/markup noise and keeps headings, lists and
tables, so an attachment costs far fewer tokens and the agent can reliably mirror
an attached document's format.

Images have two extraction paths (D-66): a **vision LLM**, routed multi-model
through the ai-client gateway (Bedrock Claude → Gemini, with the deterministic
mock as the offline sentinel), transcribes visible text *and* describes diagrams
/ UI / structure; and deterministic **OCR** (pytesseract) which needs no network
or keys. The vision path is preferred when a real provider is available and
degrades to OCR otherwise, so image understanding works both online and offline.

Every parser is optional and guarded: if a library, the OCR engine, or the
vision provider is missing, extraction degrades to a clear placeholder rather
than failing the upload.
"""

from __future__ import annotations

import base64
import io
import logging
from typing import Any

log = logging.getLogger("attach")

# How the attachment was interpreted — surfaced to the UI/audit.
KIND_TEXT = "text"
KIND_DOCUMENT = "document"
KIND_IMAGE = "image"
KIND_BINARY = "binary"

_MAX_CHARS = 200_000  # cap extracted text so one attachment can't blow the context
# The vision instruction lives in the central prompt library (D-48): template
# `attachment.vision.user` (verbatim transcription + structural description).


def _looks_like(filename: str, content_type: str, *exts: str, mime_prefix: str | None = None) -> bool:
    name = filename.lower()
    if any(name.endswith(e) for e in exts):
        return True
    return bool(mime_prefix and content_type.lower().startswith(mime_prefix))


def _tidy_md(md: str) -> str:
    """Collapse the excess blank lines a converter leaves behind."""
    import re
    return re.sub(r"\n{3,}", "\n\n", (md or "")).strip()


def _extract_pdf(raw: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw))
    return "\n\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()


def _extract_docx(raw: bytes) -> str:
    """.docx -> clean Markdown. Prefer mammoth (HTML) + markdownify so headings,
    lists and tables survive as real Markdown with no OOXML noise; fall back to
    python-docx paragraph/table text if the conversion libraries are unavailable."""
    try:
        import mammoth
        from markdownify import markdownify as _md
        html = mammoth.convert_to_html(io.BytesIO(raw)).value
        md = _tidy_md(_md(html or "", heading_style="ATX", bullets="-"))
        if md:
            return md
    except Exception as err:  # noqa: BLE001 — fall back to the basic extractor
        log.info("mammoth docx->md unavailable (%s); using python-docx", err)
    import docx  # python-docx
    doc = docx.Document(io.BytesIO(raw))
    parts: list[str] = []
    for p in doc.paragraphs:
        text = p.text.strip()
        if not text:
            continue
        style = (p.style.name or "").lower() if p.style else ""
        if style.startswith("heading"):
            level = "".join(ch for ch in style if ch.isdigit()) or "2"
            parts.append(f"{'#' * min(int(level), 6)} {text}")
        elif style.startswith("list") or style.startswith("bullet"):
            parts.append(f"- {text}")
        else:
            parts.append(text)
    for table in doc.tables:
        rows = [[c.text.strip().replace("|", "\\|") for c in row.cells] for row in table.rows]
        rows = [r for r in rows if any(r)]
        if not rows:
            continue
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        parts.append("")
        parts.append("| " + " | ".join(rows[0]) + " |")
        parts.append("| " + " | ".join(["---"] * width) + " |")
        for r in rows[1:]:
            parts.append("| " + " | ".join(r) + " |")
    return _tidy_md("\n".join(parts))


def _extract_xlsx(raw: bytes) -> str:
    """.xlsx -> one Markdown table per sheet (values only, formulas resolved)."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    out: list[str] = []
    for ws in wb.worksheets:
        rows = [["" if c is None else str(c).strip() for c in row]
                for row in ws.iter_rows(values_only=True)]
        rows = [r for r in rows if any(cell for cell in r)]
        if not rows:
            continue
        width = max(len(r) for r in rows)
        rows = [[cell.replace("|", "\\|") for cell in r] + [""] * (width - len(r)) for r in rows]
        out.append(f"## {ws.title}")
        out.append("| " + " | ".join(rows[0]) + " |")
        out.append("| " + " | ".join(["---"] * width) + " |")
        for r in rows[1:]:
            out.append("| " + " | ".join(r) + " |")
        out.append("")
    return _tidy_md("\n".join(out))


def _extract_pptx(raw: bytes) -> str:
    """.pptx -> Markdown: each slide a section, its text frames as headings/bullets."""
    from pptx import Presentation
    prs = Presentation(io.BytesIO(raw))
    out: list[str] = []
    for i, slide in enumerate(prs.slides, 1):
        out.append(f"## Slide {i}")
        for shape in slide.shapes:
            if not getattr(shape, "has_text_frame", False):
                continue
            for para in shape.text_frame.paragraphs:
                text = "".join(run.text for run in para.runs).strip()
                if text:
                    out.append(("  " * getattr(para, "level", 0)) + f"- {text}")
        out.append("")
    return _tidy_md("\n".join(out))


def _extract_image_ocr(raw: bytes) -> str:
    import pytesseract
    from PIL import Image

    img = Image.open(io.BytesIO(raw))
    return (pytesseract.image_to_string(img) or "").strip()


def extract(raw: bytes, filename: str, content_type: str) -> tuple[str, str, str]:
    """Return (text, kind, note). `text` is inlineable context ('' if none);
    `note` explains a degraded result (empty on success)."""
    filename = filename or "attachment"
    content_type = content_type or "application/octet-stream"

    # 1) plain text (fast path, no deps)
    if _looks_like(filename, content_type, ".txt", ".md", ".markdown", ".csv", ".json",
                   ".yaml", ".yml", ".log", ".xml", mime_prefix="text/"):
        try:
            return raw.decode("utf-8")[:_MAX_CHARS], KIND_TEXT, ""
        except UnicodeDecodeError:
            pass  # mislabelled binary; fall through

    # 2) documents
    if _looks_like(filename, content_type, ".pdf", mime_prefix="application/pdf"):
        try:
            txt = _extract_pdf(raw)
            return (txt[:_MAX_CHARS], KIND_DOCUMENT, "" if txt else "no extractable text in the PDF (scanned?)")
        except Exception as err:  # noqa: BLE001
            log.warning("PDF extract failed for %s: %s", filename, err)
            return "", KIND_DOCUMENT, f"could not parse the PDF ({err})"
    if _looks_like(filename, content_type, ".docx"):
        try:
            txt = _extract_docx(raw)
            return txt[:_MAX_CHARS], KIND_DOCUMENT, "" if txt else "the document had no text"
        except Exception as err:  # noqa: BLE001
            log.warning("DOCX extract failed for %s: %s", filename, err)
            return "", KIND_DOCUMENT, f"could not parse the document ({err})"
    if _looks_like(filename, content_type, ".xlsx", ".xlsm"):
        try:
            txt = _extract_xlsx(raw)
            return txt[:_MAX_CHARS], KIND_DOCUMENT, "" if txt else "the spreadsheet had no data"
        except Exception as err:  # noqa: BLE001
            log.warning("XLSX extract failed for %s: %s", filename, err)
            return "", KIND_DOCUMENT, f"could not parse the spreadsheet ({err})"
    if _looks_like(filename, content_type, ".pptx"):
        try:
            txt = _extract_pptx(raw)
            return txt[:_MAX_CHARS], KIND_DOCUMENT, "" if txt else "the presentation had no text"
        except Exception as err:  # noqa: BLE001
            log.warning("PPTX extract failed for %s: %s", filename, err)
            return "", KIND_DOCUMENT, f"could not parse the presentation ({err})"

    # 3) images -> OCR
    if _looks_like(filename, content_type, ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
                   ".tif", ".tiff", mime_prefix="image/"):
        try:
            txt = _extract_image_ocr(raw)
            return (txt[:_MAX_CHARS], KIND_IMAGE,
                    "" if txt else "no text detected in the image (OCR found nothing)")
        except Exception as err:  # noqa: BLE001 — OCR engine missing or bad image
            log.warning("image OCR failed for %s: %s", filename, err)
            return "", KIND_IMAGE, f"image not processed ({err})"

    # 4) last resort: try utf-8, else keep as an un-inlined reference
    try:
        return raw.decode("utf-8")[:_MAX_CHARS], KIND_TEXT, ""
    except UnicodeDecodeError:
        return "", KIND_BINARY, "unsupported binary type — kept as a reference, not inlined"


def is_image(filename: str, content_type: str) -> bool:
    return _looks_like(filename, content_type, ".png", ".jpg", ".jpeg", ".gif", ".bmp",
                       ".webp", ".tif", ".tiff", mime_prefix="image/")


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
        {"role": "user", "content": render_prompt("attachment.vision.user"),
         "images": [{"mimeType": mime, "dataBase64": b64}]},
    ]
    try:
        # 'recommendation' intent → vision-filtered chain leads with Bedrock, then Gemini.
        result = await llm.generate(
            intent="recommendation", messages=messages,
            temperature=0.1, max_tokens=4096, tag=tag or "attachment.vision",
        )
    except Exception as err:  # noqa: BLE001 — provider exhausted / gateway down
        log.warning("vision LLM call failed: %s", err)
        return None

    if result.provider == "mock" or not (result.content or "").strip():
        return None  # no real vision provider available → let OCR handle it
    return result.content.strip()[:_MAX_CHARS], result.provider
