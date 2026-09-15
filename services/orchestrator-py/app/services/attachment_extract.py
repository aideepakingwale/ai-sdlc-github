"""Extract usable context text from uploaded attachments.

Users attach not just plain text but **documents** (PDF, Word) and **images**
(screenshots of requirements, photos of a whiteboard, scanned specs). This turns
each into text that the context pipeline can inline into the agent prompt:

  - text/*             -> decoded as-is
  - PDF                -> extracted page text (pypdf)
  - DOCX               -> paragraph + table text (python-docx)
  - image/* -> vision LLM (, preferred) or OCR (pytesseract)
  - anything else      -> not inlined (kept as a labelled reference)

Images have two extraction paths: a **vision LLM**, routed multi-model
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
# The vision instruction lives in the central prompt library: template
# `attachment.vision.user` (verbatim transcription + structural description).


def _looks_like(filename: str, content_type: str, *exts: str, mime_prefix: str | None = None) -> bool:
    name = filename.lower()
    if any(name.endswith(e) for e in exts):
        return True
    return bool(mime_prefix and content_type.lower().startswith(mime_prefix))


def _extract_pdf(raw: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw))
    return "\n\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()


def _extract_docx(raw: bytes) -> str:
    import docx  # python-docx

    doc = docx.Document(io.BytesIO(raw))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts).strip()


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
). Returns (text, provider) on success, or None when no *real* vision
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
