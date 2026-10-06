"""Document ingestion entry point: any file in, structured Markdown out.

    parse (thread)  ->  describe figures (vision / OCR, bounded)  ->  merge in place

`ingest()` is the only function the API needs. It never raises for a file it cannot
fully read: it returns what it could read plus warnings, and raises
:class:`IngestError` only when nothing at all is usable (corrupt / unsupported).
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
from pathlib import PurePath

from . import soffice
from .diagrams import parse_drawio, parse_svg, parse_vsdx
from .figures import Describer, apply_descriptions, describe_figures, strip_markers
from .images import normalise
from .model import IngestError, Limits, RichDocument, marker, tidy
from .office import parse_docx, parse_pptx, parse_xlsx
from .pdf import parse_pdf

log = logging.getLogger("attach.ingest")

TEXT_EXTS = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml", ".log", ".xml", ".rst",
             ".adoc", ".ini", ".cfg", ".toml", ".properties", ".sql", ".puml", ".plantuml", ".mmd",
             ".mermaid", ".dbml", ".tf", ".proto", ".graphql", ".feature", ".gherkin", ".env.example"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
LEGACY = {".doc": ("doc", "docx"), ".dot": ("dot", "docx"), ".rtf": ("rtf", "docx"), ".odt": ("odt", "docx"),
          ".ppt": ("ppt", "pptx"), ".pps": ("pps", "pptx"), ".odp": ("odp", "pptx"),
          ".xls": ("xls", "xlsx"), ".ods": ("ods", "xlsx")}

# Everything the platform can read, for the UI's file picker and the error messages.
SUPPORTED_DESCRIPTION = (
    "PDF, Word (.docx), PowerPoint (.pptx), Excel (.xlsx), draw.io, Visio (.vsdx), SVG, images "
    "(PNG/JPG/GIF/WebP/TIFF), HTML, Markdown/text/CSV/JSON/YAML/SQL/PlantUML/Mermaid; "
    "legacy .doc/.ppt/.xls/.rtf/.odt/.odp/.ods when LibreOffice is installed"
)


def _ext(filename: str) -> str:
    return PurePath(filename or "").suffix.lower()


def _decode(raw: bytes) -> str | None:
    for enc in ("utf-8-sig", "utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    try:
        text = raw.decode("cp1252")
    except UnicodeDecodeError:
        return None
    return text if "\x00" not in text else None


def _parse_html(raw: bytes, limits: Limits) -> RichDocument:
    text = _decode(raw) or ""
    text = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", text)
    doc = RichDocument(kind="document")
    try:
        from markdownify import markdownify
        md = markdownify(text, heading_style="ATX", bullets="-")
    except ImportError:
        md = re.sub(r"<[^>]+>", " ", text)
    doc.markdown = tidy(re.sub(r"!\[[^\]]*\]\((?:data:[^)]{0,64})[^)]*\)", "", md))
    return doc


def _parse_image(raw: bytes, filename: str, limits: Limits) -> RichDocument:
    norm = normalise(raw, _ext(filename))
    if norm is None:
        raise IngestError("the image could not be decoded (unsupported or corrupt)")
    doc = RichDocument(kind="image")
    fig = doc.new_figure(norm[0], norm[1], "image", width=norm[2], height=norm[3], kind="image")
    doc.markdown = marker(fig.id)
    doc.stats["images"] = 1
    return doc


def _legacy(raw: bytes, ext: str, limits: Limits) -> RichDocument:
    src, target = LEGACY[ext]
    if not soffice.available():
        raise IngestError(f"{ext} files need LibreOffice, which is not installed on this server - "
                          f"please save the file as .{target} and upload it again")
    converted = soffice.convert(raw, src, target)
    if not converted:
        raise IngestError(f"the {ext} file could not be converted - please save it as .{target} and upload it again")
    parser = {"docx": parse_docx, "pptx": parse_pptx, "xlsx": parse_xlsx}[target]
    doc = parser(converted, limits)
    doc.warnings.insert(0, f"converted from {ext} via LibreOffice")
    return doc


def parse(raw: bytes, filename: str, content_type: str, limits: Limits) -> RichDocument:
    """Synchronous parse of any supported file (run in a worker thread)."""
    ext = _ext(filename)
    ctype = (content_type or "").lower()

    if ext == ".pdf" or ctype == "application/pdf" or raw[:5] == b"%PDF-":
        return parse_pdf(raw, limits)
    if ext in (".docx", ".docm"):
        return parse_docx(raw, limits)
    if ext in (".pptx", ".pptm"):
        return parse_pptx(raw, limits)
    if ext in (".xlsx", ".xlsm"):
        return parse_xlsx(raw, limits)
    if ext == ".vsdx":
        return parse_vsdx(raw, limits)
    if ext == ".svg" or ctype == "image/svg+xml":
        return parse_svg(raw, limits)
    if ext in (".drawio", ".dio") or (ext in (".xml", "") and b"<mxfile" in raw[:2048]):
        return parse_drawio(raw, limits)
    if ext in (".html", ".htm", ".xhtml"):
        return _parse_html(raw, limits)
    if ext in LEGACY:
        return _legacy(raw, ext, limits)
    if ext in IMAGE_EXTS or ctype.startswith("image/"):
        return _parse_image(raw, filename, limits)
    # Content sniffing for files with a missing / wrong extension.
    if raw[:4] == b"PK\x03\x04":
        for guess, parser in (("word/document.xml", parse_docx), ("ppt/presentation.xml", parse_pptx),
                              ("xl/workbook.xml", parse_xlsx)):
            try:
                import zipfile
                if guess in zipfile.ZipFile(io.BytesIO(raw)).namelist():
                    return parser(raw, limits)
            except Exception:  # noqa: BLE001
                break
    if ext in TEXT_EXTS or ctype.startswith("text/") or ext == "" or ctype in ("application/json", "application/xml"):
        text = _decode(raw)
        if text is not None:
            return RichDocument(kind="text", markdown=text)
    text = _decode(raw) if not raw[:512].count(b"\x00") else None
    if text is not None:
        return RichDocument(kind="text", markdown=text)
    return RichDocument(kind="binary", warnings=[f"unsupported file type ({ext or ctype or 'unknown'}) - kept as a "
                                                 f"reference, not inlined. Supported: {SUPPORTED_DESCRIPTION}"])


async def ingest(raw: bytes, filename: str, content_type: str, *, limits: Limits,
                 describer: Describer | None = None) -> RichDocument:
    doc = await asyncio.wait_for(asyncio.to_thread(parse, raw, filename, content_type, limits),
                                 timeout=limits.max_seconds + 30)
    if doc.figures:
        if describer is not None:
            apply_descriptions(doc, await describe_figures(doc, describer, limits))
        else:
            strip_markers(doc)
    if len(doc.markdown) > limits.max_chars:
        doc.warnings.append(f"the extracted text was cut at {limits.max_chars:,} characters "
                            f"(of {len(doc.markdown):,})")
        doc.markdown = doc.markdown[: limits.max_chars]
    doc.stats["chars"] = len(doc.markdown)
    return doc
