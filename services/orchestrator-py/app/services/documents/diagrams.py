"""Diagram files: draw.io, Visio (.vsdx) and SVG -> structured text.

A diagram is mostly *structure*: boxes, containers and which box points at which.
These formats store that structure as XML, so it can be recovered exactly - no
model needed. The result is Markdown listing components, groupings and directed
connections ("Client -> API Gateway [HTTPS]").
"""
from __future__ import annotations

import base64
import html
import re
import urllib.parse
import zlib

from .model import IngestError, Limits, RichDocument, tidy
from .safety import check_zip, parse_xml

_TAG_RE = re.compile(r"<[^>]+>")


def _plain(value: str) -> str:
    """Strip the HTML draw.io stores in labels."""
    text = html.unescape(_TAG_RE.sub(" ", html.unescape(value or "")))
    return re.sub(r"\s+", " ", text).strip()


# ------------------------------------------------------------------ draw.io
def _drawio_models(xml: bytes) -> list[tuple[str, bytes]]:
    root = parse_xml(xml)
    models: list[tuple[str, bytes]] = []
    for d in root.iter("diagram"):
        name = d.get("name") or f"Page {len(models) + 1}"
        inner = d.find("mxGraphModel")
        if inner is not None:
            from lxml import etree
            models.append((name, etree.tostring(inner)))
        elif (d.text or "").strip():                      # compressed: base64(deflate(urlencoded xml))
            try:
                data = zlib.decompress(base64.b64decode(d.text.strip()), -15)
                models.append((name, urllib.parse.unquote(data.decode("utf-8")).encode("utf-8")))
            except Exception:  # noqa: BLE001
                continue
    if not models and root.tag == "mxGraphModel":
        from lxml import etree
        models.append(("Diagram", etree.tostring(root)))
    return models


def parse_drawio(raw: bytes, limits: Limits) -> RichDocument:
    try:
        models = _drawio_models(raw)
    except Exception as err:  # noqa: BLE001
        raise IngestError("could not read the draw.io file") from err
    if not models:
        raise IngestError("the draw.io file contains no diagram")
    doc = RichDocument(kind="diagram")
    out: list[str] = []
    for name, model_xml in models[: limits.max_pages]:
        root = parse_xml(model_xml)
        cells = {c.get("id"): c for c in root.iter("mxCell")}
        label = {cid: _plain(c.get("value") or "") for cid, c in cells.items()}
        nodes = [cid for cid, c in cells.items() if c.get("vertex") == "1" and label.get(cid)]
        edges = [c for c in cells.values() if c.get("edge") == "1"]
        out.append(f"## Diagram: {name}")
        if nodes:
            out.append("Components:")
            for cid in nodes:
                parent = cells[cid].get("parent")
                inside = f" (inside {label[parent]})" if parent in label and label.get(parent) and parent != cid else ""
                out.append(f"- {label[cid]}{inside}")
        links: list[str] = []
        for e in edges:
            a, b = label.get(e.get("source") or ""), label.get(e.get("target") or "")
            if a and b:
                text = _plain(e.get("value") or "")
                style = e.get("style") or ""
                arrow = "—" if "endArrow=none" in style and "startArrow=none" in style else "→"
                links.append(f"- {a} {arrow} {b}" + (f" [{text}]" if text else ""))
        if links:
            out.append("Connections:")
            out += links
        doc.bump("components", len(nodes))
        doc.bump("diagram_edges", len(links))
        out.append("")
    doc.stats["diagrams"] = len(models)
    doc.markdown = tidy("\n".join(out))
    return doc


# ------------------------------------------------------------------ SVG
def parse_svg(raw: bytes, limits: Limits) -> RichDocument:
    try:
        root = parse_xml(raw)
    except Exception as err:  # noqa: BLE001
        raise IngestError("could not read the SVG file") from err
    texts: list[str] = []
    title = desc = ""
    for el in root.iter():
        tag = el.tag.split("}")[-1] if isinstance(el.tag, str) else ""
        if tag == "title" and not title:
            title = _plain(el.text or "")
        elif tag == "desc" and not desc:
            desc = _plain(el.text or "")
        elif tag in ("text", "tspan"):
            t = _plain("".join(el.itertext()))
            if t and t not in texts:
                texts.append(t)
    doc = RichDocument(kind="diagram")
    lines = ["## SVG graphic"]
    if title:
        lines.append(f"Title: {title}")
    if desc:
        lines.append(f"Description: {desc}")
    if texts:
        lines.append("Text in the graphic:")
        lines += [f"- {t}" for t in texts[:400]]
    doc.markdown = tidy("\n".join(lines))
    doc.stats["texts"] = len(texts)
    return doc


# ------------------------------------------------------------------ Visio
def parse_vsdx(raw: bytes, limits: Limits) -> RichDocument:
    zf = check_zip(raw, limits)
    pages = sorted(n for n in zf.namelist() if re.fullmatch(r"visio/pages/page\d+\.xml", n))
    if not pages:
        raise IngestError("the Visio file contains no pages")
    ns = "{http://schemas.microsoft.com/office/visio/2012/main}"
    doc = RichDocument(kind="diagram")
    out: list[str] = []
    for pn, name in enumerate(pages[: limits.max_pages], start=1):
        root = parse_xml(zf.read(name))
        labels: dict[str, str] = {}
        for sh in root.iter(f"{ns}Shape"):
            txt = sh.find(f"{ns}Text")
            if txt is not None:
                t = _plain("".join(txt.itertext()))
                if t:
                    labels[sh.get("ID", "")] = t
        edges: list[tuple[str, str]] = []
        ends: dict[str, dict[str, str]] = {}
        for c in root.iter(f"{ns}Connect"):
            cell = c.get("FromCell", "")
            ends.setdefault(c.get("FromSheet", ""), {})["begin" if "Begin" in cell else "end"] = c.get("ToSheet", "")
        for sheet, e in ends.items():
            a, b = labels.get(e.get("begin", "")), labels.get(e.get("end", ""))
            if a and b:
                edges.append((a, b))
        out.append(f"## Visio page {pn}")
        out += [f"- {t}" for sid, t in labels.items() if all(sid != s for s in ends)]
        if edges:
            out.append("Connections:")
            out += [f"- {a} → {b}" for a, b in edges]
        doc.bump("diagram_edges", len(edges))
        out.append("")
    doc.stats["diagrams"] = min(len(pages), limits.max_pages)
    doc.markdown = tidy("\n".join(out))
    return doc
