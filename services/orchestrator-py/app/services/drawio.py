"""Professional draw.io (diagrams.net) architecture diagrams.

The Solution/Technical Architect subagent produces an EDITABLE `.drawio`
(mxGraph XML) diagram — every box, label and connector a real, movable shape
that opens in diagrams.net, the desktop app or the VS Code extension.

Two deterministic (zero-token) tools live here:

  - `cloud_arch_to_drawio(spec)` — convert the CloudArchitecture spec the SA/TA
    agents already emit (nodes / edges / nested clusters) into a professionally laid-out
    `.drawio`:
        * the TARGET CLOUD is inferred (declared → vendor service keys → project text) and
          every icon comes from THAT cloud (see cloud_catalog) — an Azure solution never
          gets AWS icons; a service with no icon on the target cloud becomes a neutral box;
        * a flow-ordered layout (users → edge → compute → data, left→right or top→down),
          not a grid; nested clusters (VPC → subnet) are real draw.io containers, inside a
          cloud boundary frame; users/external systems sit outside the frame;
        * title + target-platform subtitle, a legend, labelled icons (name + official
          service name), orthogonal edges with white label backgrounds, async edges dashed.
    Because it reuses the structured output, every architecture stage gains an editable
    draw.io at NO extra model cost.
  - `validate_drawio(xml)` — a structural + quality validator that returns actionable
    findings, so a hand-authored diagram (the LLM authoring skill) can be checked and
    self-corrected the way OpenAPI is Spectral-linted. It also flags MIXED cloud icon
    libraries and missing title/legend.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html import escape
from typing import Any

from . import cloud_catalog as cat

_GAP = 30                       # minimum gap between shapes (validator + layout)
_ICON = 64                      # icon size
_NODE_W, _ICON_FOOT_H, _BOX_H = 140, 104, 60
_COL_GAP, _ROW_GAP = 70, 34
_PAD, _HEADER, _FRAME_HEADER = 26, 34, 44
_MARGIN, _TITLE_H = 40, 70
_INK, _MUTED, _EDGE = "#1F2937", "#6B7280", "#44546A"
_ASYNC_RX = re.compile(r"\b(async|event|publish|subscrib|message|notify|stream|queue|enqueue|webhook)\w*", re.I)


@dataclass
class _Block:
    kind: str                       # node | cluster | frame
    id: str
    w: int = 0
    h: int = 0
    x: int = 0                      # relative to the parent block
    y: int = 0
    layer: int = 0
    children: list[_Block] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


def _layers(nodes: list[dict], edges: list[dict], actor_ids: set[str]) -> dict[str, int]:
    """Flow layer per node: longest path from the sources (cycles tolerated, actors first)."""
    ids = [n["id"] for n in nodes]
    layer = {i: 0 for i in ids}
    cap = max(len(ids) - 1, 0)
    for _ in range(len(ids)):
        changed = False
        for e in edges:
            a, b = e.get("fromId"), e.get("toId")
            if a in layer and b in layer and a != b and layer[b] < layer[a] + 1 and layer[a] + 1 <= cap:
                layer[b] = layer[a] + 1
                changed = True
        if not changed:
            break
    for i in actor_ids:
        layer[i] = 0
    return layer


def _arrange(children: list[_Block], horizontal: bool) -> tuple[int, int]:
    """Place `children` in flow order: one column per layer (LR) / row per layer (TB),
    items of a layer stacked, each column centred on the tallest. Returns content size."""
    if not children:
        return 0, 0
    groups: dict[int, list[_Block]] = {}
    for c in children:
        groups.setdefault(c.layer, []).append(c)
    ordered = [groups[k] for k in sorted(groups)]
    gap_main, gap_cross = (_COL_GAP, _ROW_GAP) if horizontal else (_ROW_GAP + 20, _COL_GAP - 20)
    cross_sizes = []
    for grp in ordered:
        total = sum((b.h if horizontal else b.w) for b in grp) + gap_cross * (len(grp) - 1)
        cross_sizes.append(total)
    cross_max = max(cross_sizes)
    cursor = 0
    for grp, cross in zip(ordered, cross_sizes, strict=True):
        main = max((b.w if horizontal else b.h) for b in grp)
        pos = (cross_max - cross) // 2
        for b in grp:
            if horizontal:
                b.x, b.y = cursor + (main - b.w) // 2, pos
                pos += b.h + gap_cross
            else:
                b.x, b.y = pos, cursor + (main - b.h) // 2
                pos += b.w + gap_cross
        cursor += main + gap_main
    cursor -= gap_main
    return (cursor, cross_max) if horizontal else (cross_max, cursor)


def _size_container(blk: _Block, horizontal: bool, header: int) -> None:
    cw, ch = _arrange(blk.children, horizontal)
    for c in blk.children:
        c.x += _PAD
        c.y += header + _PAD // 2
    blk.w = max(cw + 2 * _PAD, 180)
    blk.h = max(ch + header + _PAD + _PAD // 2, 110)


def _cluster_look(label: str, provider: str) -> dict[str, str]:
    """Boundary styling by what the cluster IS (public/private subnet, network, zone)."""
    t = label.lower()
    if re.search(r"public|dmz|edge", t):
        return {"fill": "#EDF7E8", "stroke": "#7AA116", "dash": "0", "font": "#527A0C"}
    if re.search(r"private|data|backend|isolated", t):
        return {"fill": "#E8F3FA", "stroke": "#147EBA", "dash": "0", "font": "#0E5F8C"}
    if re.search(r"zone|\baz\b|availability|region", t):
        return {"fill": "none", "stroke": "#8A94A6", "dash": "1", "font": "#5B6478"}
    if re.search(r"vpc|vnet|network|virtual", t):
        stroke = {"aws": "#248814", "azure": "#0078D4", "gcp": "#4285F4"}.get(provider, "#5B6478")
        return {"fill": "none", "stroke": stroke, "dash": "0", "font": stroke}
    return {"fill": "none", "stroke": "#8A94A6", "dash": "1", "font": "#5B6478"}


def cloud_arch_to_drawio(
    spec: dict[str, Any], *, title: str | None = None, context: list[str] | None = None,
) -> str:
    """Convert a CloudArchitecture spec (dict) into an editable .drawio document.
    Deterministic — no model call. `context` = project text (tech stack, requirements, …)
    used only to infer the target cloud. Returns mxGraph XML (`<mxfile>…</mxfile>`)."""
    title = title or spec.get("title") or "Architecture"
    nodes = [n for n in (spec.get("nodes") or []) if n.get("id")]
    edges = list(spec.get("edges") or [])
    clusters = [c for c in (spec.get("clusters") or []) if c.get("id")]
    horizontal = (spec.get("direction") or "LR").upper() != "TB"

    choice = cat.infer_provider(
        spec.get("provider"),
        [title, *(context or []), *(c.get("label", "") for c in clusters), *(n.get("label", "") for n in nodes)],
        [n.get("service", "") for n in nodes],
    )
    provider = choice.provider
    pname, pstroke, pfill, pheader = cat.PROVIDER_THEME[provider]

    resolved = {n["id"]: cat.resolve(provider, n.get("service", "")) for n in nodes}
    actor_ids = {i for i, r in resolved.items() if r.tier == "actor"}
    layer = _layers(nodes, edges, actor_ids)

    cluster_ids = {c["id"] for c in clusters}
    parent_of = {c["id"]: (c.get("parent") if c.get("parent") in cluster_ids and c.get("parent") != c["id"] else "")
                 for c in clusters}
    blocks: dict[str, _Block] = {}
    for c in clusters:
        blocks[c["id"]] = _Block("cluster", c["id"], data={"label": c.get("label", c["id"])})
    for n in nodes:
        r = resolved[n["id"]]
        icon = cat.icon_data_uri(r.icon_path) if r.icon_path else None
        blk = _Block("node", n["id"], layer=layer[n["id"]], data={"n": n, "r": r, "icon": icon})
        blk.w = _NODE_W
        blk.h = _ICON_FOOT_H if icon else _BOX_H
        blocks[n["id"]] = blk

    top: list[_Block] = []
    for c in clusters:
        (blocks[parent_of[c["id"]]].children if parent_of[c["id"]] else top).append(blocks[c["id"]])
    for n in nodes:
        grp = n.get("group") if n.get("group") in cluster_ids else ""
        if n["id"] in actor_ids and not grp:
            continue
        (blocks[grp].children if grp else top).append(blocks[n["id"]])
    actors = [blocks[i] for i in sorted(actor_ids, key=lambda i: [x["id"] for x in nodes].index(i))
              if blocks[i].id not in {ch.id for b in blocks.values() for ch in b.children}]

    def finish(b: _Block) -> None:
        if b.kind != "cluster":
            return
        for ch in b.children:
            finish(ch)
        _size_container(b, horizontal, _HEADER)
        b.layer = min((ch.layer for ch in b.children), default=0)
    for b in top:
        finish(b)

    # Cloud boundary frame around everything that lives in the cloud; actors stay outside.
    frame = _Block("frame", "frame", children=top)
    _size_container(frame, horizontal, _FRAME_HEADER)
    outer: list[_Block] = []
    for a in actors:
        a.layer = -1
    outer.extend(actors)
    frame.layer = 0
    outer.append(frame)
    ow, oh = _arrange(outer, horizontal)
    ox, oy = _MARGIN, _MARGIN + _TITLE_H

    # ---- emit cells ------------------------------------------------------------
    cells: list[str] = []
    abs_pos: dict[str, tuple[int, int]] = {}

    platform = pname if provider != "generic" else "cloud-neutral"
    cells.append(_cell(
        "title",
        f"<b>{escape(title)}</b><br><font color=\"{_MUTED}\" style=\"font-size:12px\">Target platform: {escape(platform)}</font>",
        f"text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=top;whiteSpace=wrap;fontSize=20;fontColor={_INK};",
        "1", _MARGIN, _MARGIN - 10, max(ow, 520), _TITLE_H - 10))

    def emit(b: _Block, parent_id: str, px: int, py: int, depth: int) -> None:
        ax, ay = px + b.x, py + b.y
        # children of a container use coordinates RELATIVE to it; top-level cells use page coordinates
        rx, ry = (ax, ay) if parent_id == "1" else (b.x, b.y)
        if b.kind == "node":
            n, r, icon = b.data["n"], b.data["r"], b.data["icon"]
            label = str(n.get("label") or n["id"])
            sub = r.service_name if r.service_name and r.service_name.lower() != label.lower() else ""
            html = f"<b>{escape(label)}</b>" + (f'<br><font color="{_MUTED}" style="font-size:10px">{escape(sub)}</font>' if sub else "")
            cid = f"n_{_safe(n['id'])}"
            if icon:
                gx = rx + (b.w - _ICON) // 2
                style = (f"shape=image;image={icon};imageAspect=0;aspect=fixed;html=1;whiteSpace=wrap;"
                         f"verticalLabelPosition=bottom;verticalAlign=top;labelPosition=center;align=center;"
                         f"fontSize=11;fontColor={_INK};")
                cells.append(_cell(cid, html, style, parent_id, gx, ry, _ICON, _ICON))
                abs_pos[n["id"]] = (ax + (b.w - _ICON) // 2, ay)
                b.data["rect"] = (ax + (b.w - _ICON) // 2, ay, _ICON, _ICON)
                b.data["obst"] = (ax, ay, b.w, _ICON + 42)            # icon + its two-line label
                b.data["cid"] = cid
            else:
                _, fill, stroke = cat.TIER_STYLE.get(r.tier, cat.TIER_STYLE["compute"])
                style = (f"rounded=1;arcSize=12;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};"
                         f"fontSize=12;fontColor={_INK};")
                cells.append(_cell(cid, html, style, parent_id, rx, ry, b.w, _BOX_H - 8))
                abs_pos[n["id"]] = (ax, ay)
                b.data["rect"] = b.data["obst"] = (ax, ay, b.w, _BOX_H - 8)
            b.data["cid"] = cid
            return
        if b.kind == "frame":
            cid = "frame"
            style = (f"rounded=1;arcSize=1;whiteSpace=wrap;html=1;fillColor={pfill};strokeColor={pstroke};strokeWidth=2;"
                     f"verticalAlign=top;align=left;spacingLeft=14;spacingTop=8;fontStyle=1;fontSize=15;fontColor={pheader};"
                     f"container=1;collapsible=0;recursiveResize=0;")
            cells.append(_cell(cid, escape(pname), style, parent_id, rx, ry, b.w, b.h))
        else:
            look = _cluster_look(str(b.data["label"]), provider)
            cid = f"c_{_safe(b.id)}"
            style = (f"rounded=1;arcSize=2;whiteSpace=wrap;html=1;fillColor={look['fill']};strokeColor={look['stroke']};"
                     f"strokeWidth=1.5;dashed={look['dash']};verticalAlign=top;align=left;spacingLeft=10;spacingTop=4;"
                     f"fontStyle=1;fontSize=12;fontColor={look['font']};container=1;collapsible=0;recursiveResize=0;")
            cells.append(_cell(cid, escape(str(b.data["label"])), style, parent_id, rx, ry, b.w, b.h))
        b.data["cid"] = cid
        for ch in b.children:
            emit(ch, cid, ax, ay, depth + 1)

    for a in actors:
        emit(a, "1", ox, oy, 0)
    emit(frame, "1", ox, oy, 0)

    # ---- edges: routed around shapes, waypoints written into the file ---------------------
    by_id = {n["id"]: blocks[n["id"]] for n in nodes}
    bounds = (ox + ow + _MARGIN * 2, oy + oh + _MARGIN * 2)
    all_obst = {i: blk.data["obst"] for i, blk in by_id.items() if "obst" in blk.data}
    for i, e in enumerate(edges):
        a, b = by_id.get(e.get("fromId")), by_id.get(e.get("toId"))
        if not a or not b or "cid" not in a.data or "cid" not in b.data or a.id == b.id:
            continue
        label = str(e.get("label", "") or "")
        asyn = bool(_ASYNC_RX.search(label)) or (resolved[b.id].tier == "integration" and resolved[a.id].tier != "integration")
        ra, rb = a.data["rect"], b.data["rect"]
        dx = (rb[0] + rb[2] / 2) - (ra[0] + ra[2] / 2)
        dy = (rb[1] + rb[3] / 2) - (ra[1] + ra[3] / 2)
        go_h = (abs(dx) >= abs(dy)) if not (layer[b.id] > layer[a.id]) else horizontal
        if go_h:
            sa, sb = ("r", "l") if dx >= 0 else ("l", "r")
        else:
            sa, sb = ("b", "t") if dy >= 0 else ("t", "b")

        def port(rect: tuple[int, int, int, int], side: str) -> tuple[int, int]:
            x, y, w, h = rect
            return {"r": (x + w, y + h // 2), "l": (x, y + h // 2), "t": (x + w // 2, y), "b": (x + w // 2, y + h)}[side]
        p0, p1 = port(ra, sa), port(rb, sb)
        out = {"r": (12, 0), "l": (-12, 0), "t": (0, -12), "b": (0, 12)}
        q0 = (p0[0] + out[sa][0], p0[1] + out[sa][1])
        q1 = (p1[0] + out[sb][0], p1[1] + out[sb][1])
        others = [r for k, r in all_obst.items() if k not in (a.id, b.id)]
        path = _route(q0, q1, others, bounds)
        points = ""
        if path and len(path) > 2:
            pts = "".join(f'<mxPoint x="{x}" y="{y}"/>' for x, y in path)
            points = f'<Array as="points">{pts}</Array>'
        ex = {"r": "exitX=1;exitY=0.5;", "l": "exitX=0;exitY=0.5;", "b": "exitX=0.5;exitY=1;", "t": "exitX=0.5;exitY=0;"}[sa]
        en = {"r": "entryX=1;entryY=0.5;", "l": "entryX=0;entryY=0.5;", "b": "entryX=0.5;entryY=1;", "t": "entryX=0.5;entryY=0;"}[sb]
        style = (f"edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;endArrow=block;endFill=1;"
                 f"strokeColor={_EDGE};strokeWidth=2;fontSize=11;fontColor={_INK};labelBackgroundColor=#FFFFFF;"
                 f"{'dashed=1;dashPattern=6 4;' if asyn else ''}{ex}{en}exitDx=0;exitDy=0;entryDx=0;entryDy=0;")
        cells.append(
            f'<mxCell id="e_{i}" value="{escape(label)}" style="{style}" edge="1" parent="1" '
            f'source="{a.data["cid"]}" target="{b.data["cid"]}"><mxGeometry relative="1" as="geometry">{points}</mxGeometry></mxCell>')

    # ---- legend ------------------------------------------------------------------
    total_w, total_h = ox + ow + _MARGIN, oy + oh + _MARGIN
    leg_y = oy + oh + 16
    legend = (f"<b>Legend</b><br>─▶ synchronous request &nbsp;&nbsp; ┄▶ asynchronous / event"
              f"<br>Icons: {escape(pname if provider != 'generic' else 'neutral shapes (no single cloud identified)')}")
    cells.append(_cell("legend", legend,
                       f"text;html=1;strokeColor=#D0D5DD;fillColor=#FFFFFF;align=left;verticalAlign=top;whiteSpace=wrap;"
                       f"spacing=8;fontSize=11;fontColor={_MUTED};rounded=1;", "1", _MARGIN, leg_y, 330, 64))
    total_h = max(total_h, leg_y + 64 + _MARGIN)

    body = "".join(cells)
    return (
        f'<mxfile host="ai-sdlc" provider="{provider}">'
        f'<diagram name="{escape(title)}">'
        f'<mxGraphModel dx="1100" dy="800" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" '
        f'arrows="1" fold="1" page="1" pageScale="1" pageWidth="{max(total_w, 1100)}" pageHeight="{max(total_h, 800)}" '
        f'math="0" shadow="0">'
        f'<root><mxCell id="0"/><mxCell id="1" parent="0"/>{body}</root></mxGraphModel></diagram></mxfile>'
    )



def _route(
    start: tuple[int, int], end: tuple[int, int], obstacles: list[tuple[int, int, int, int]],
    bounds: tuple[int, int], step: int = 10,
) -> list[tuple[int, int]] | None:
    """Orthogonal path from `start` to `end` that avoids `obstacles` (x, y, w, h), preferring few
    turns (A* on a coarse grid). Returns the corner points incl. endpoints, or None."""
    import heapq

    def cell(pt: tuple[int, int]) -> tuple[int, int]:
        return (round(pt[0] / step), round(pt[1] / step))

    gw, gh = bounds[0] // step + 4, bounds[1] // step + 4
    blocked: set[tuple[int, int]] = set()
    for ox, oy, ow, oh in obstacles:
        for gx in range(int(ox // step) - 0, int((ox + ow) // step) + 2):
            for gy in range(int(oy // step) - 0, int((oy + oh) // step) + 2):
                if ox - step * 0.5 <= gx * step <= ox + ow + step * 0.5 and oy - step * 0.5 <= gy * step <= oy + oh + step * 0.5:
                    blocked.add((gx, gy))
    s, t = cell(start), cell(end)
    blocked.discard(s)
    blocked.discard(t)
    dirs = [(1, 0), (-1, 0), (0, 1), (0, -1)]
    heap: list[tuple[float, int, float, tuple[int, int], int]] = [(0.0, 0, 0.0, s, -1)]
    best: dict[tuple[tuple[int, int], int], float] = {(s, -1): 0.0}
    prev: dict[tuple[tuple[int, int], int], tuple[tuple[int, int], int] | None] = {(s, -1): None}
    counter = 0
    while heap:
        _, _, cost, cur, d = heapq.heappop(heap)
        if cur == t:
            pts, key = [], (cur, d)
            while key is not None:
                pts.append((key[0][0] * step, key[0][1] * step))
                key = prev[key]
            pts.reverse()
            simp = [pts[0]]
            for i in range(1, len(pts) - 1):
                a, b, c = simp[-1], pts[i], pts[i + 1]
                if not ((a[0] == b[0] == c[0]) or (a[1] == b[1] == c[1])):
                    simp.append(b)
            simp.append(pts[-1])
            simp[0], simp[-1] = start, end
            return simp
        if cost > best.get((cur, d), 1e18):
            continue
        for ndir, (dx, dy) in enumerate(dirs):
            nx, ny = cur[0] + dx, cur[1] + dy
            if not (0 <= nx < gw and 0 <= ny < gh) or (nx, ny) in blocked:
                continue
            ncost = cost + 1 + (6 if d != -1 and d != ndir else 0)
            key = ((nx, ny), ndir)
            if ncost < best.get(key, 1e18):
                best[key] = ncost
                prev[key] = (cur, d)
                counter += 1
                heapq.heappush(heap, (ncost + abs(nx - t[0]) + abs(ny - t[1]), counter, ncost, (nx, ny), ndir))
    return None


def _cell(cell_id: str, html_value: str, style: str, parent: str, x: int, y: int, w: int, h: int) -> str:
    """`html_value` is HTML source (callers escape user text inside it); it is attribute-escaped once here."""
    return (
        f'<mxCell id="{cell_id}" value="{escape(html_value, quote=True)}" style="{style}" vertex="1" parent="{parent}">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
    )


def _vertex(cell_id: str, label: str, style: str, x: int, y: int, w: int, h: int) -> str:
    return (
        f'<mxCell id="{cell_id}" value="{escape(label)}" style="{style}" vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
    )


def _safe(raw: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(raw)) or "x"


# ---------------------------------------------------------------- validator (the tool)
def validate_drawio(xml: str) -> dict[str, Any]:
    """Structurally + qualitatively validate a .drawio (mxGraph XML) document.
    Deterministic and zero-token. Returns {ok, errors, warnings, stats} with
    actionable messages the architect (or the authoring LLM) can act on."""
    errors: list[str] = []
    warnings: list[str] = []

    text = (xml or "").strip()
    if not text:
        return {"ok": False, "errors": ["Diagram is empty."], "warnings": [], "stats": {}}

    try:
        root = ET.fromstring(text)
    except ET.ParseError as err:
        return {"ok": False, "errors": [f"Not well-formed XML: {err}"], "warnings": [], "stats": {}}

    # Accept <mxfile> or a bare <mxGraphModel>.
    model = root if root.tag == "mxGraphModel" else root.find(".//mxGraphModel")
    if model is None:
        return {"ok": False, "errors": ["Missing <mxGraphModel> — not a draw.io document."],
                "warnings": [], "stats": {}}
    graph_root = model.find("root")
    if graph_root is None:
        return {"ok": False, "errors": ["Missing <root> element inside <mxGraphModel>."],
                "warnings": [], "stats": {}}

    cells = graph_root.findall("mxCell")
    ids: list[str] = [c.get("id", "") for c in cells]
    id_set = set(ids)

    # Required base cells.
    for base in ("0", "1"):
        if base not in id_set:
            errors.append(f"Missing required base cell id=\"{base}\" (draw.io needs <mxCell id=\"0\"/> and "
                          f"<mxCell id=\"1\" parent=\"0\"/>).")
    # Unique ids.
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        errors.append(f"Duplicate cell id(s): {', '.join(dupes)} — every cell needs a unique id.")

    vertices = [c for c in cells if c.get("vertex") == "1"]
    edges = [c for c in cells if c.get("edge") == "1"]
    by_id = {c.get("id", ""): c for c in cells}

    def style_of(c: ET.Element) -> str:
        return c.get("style") or ""

    def is_text(c: ET.Element) -> bool:        # title / legend / annotation cells
        st = style_of(c)
        return st.startswith("text;") or "shape=text" in st

    def is_container(c: ET.Element) -> bool:
        st = style_of(c)
        return "container=1" in st or "swimlane" in st or ("dashed=1" in st and "fillColor=none" in st)

    def absolute(c: ET.Element) -> tuple[float, float, float, float] | None:
        """(x, y, w, h) in page coordinates — child coordinates are relative to a container parent."""
        geo = c.find("mxGeometry")
        if geo is None:
            return None
        try:
            x, y = float(geo.get("x", 0)), float(geo.get("y", 0))
            w, h = float(geo.get("width", 0)), float(geo.get("height", 0))
        except ValueError:
            return None
        seen, p = set(), c.get("parent")
        while p and p not in ("0", "1") and p in by_id and p not in seen:
            seen.add(p)
            pg = by_id[p].find("mxGeometry")
            if pg is not None:
                try:
                    x += float(pg.get("x", 0))
                    y += float(pg.get("y", 0))
                except ValueError:
                    pass
            p = by_id[p].get("parent")
        return x, y, w, h

    if not vertices:
        errors.append("No shapes (vertex cells) — the diagram has nothing to show.")

    for c in vertices:
        cid = c.get("id", "?")
        geo = c.find("mxGeometry")
        if geo is None:
            errors.append(f"Shape '{cid}' has no <mxGeometry> — it cannot be placed.")
            continue
        try:
            w, h = float(geo.get("width", 0)), float(geo.get("height", 0))
        except ValueError:
            errors.append(f"Shape '{cid}' has non-numeric geometry size.")
            continue
        if w <= 0 or h <= 0:
            errors.append(f"Shape '{cid}' has zero/negative size (width={w}, height={h}).")
        if not (c.get("value") or "").strip():
            warnings.append(f"Shape '{cid}' has no label — professional diagrams name every element.")

    connected: set[str] = set()
    for c in edges:
        cid = c.get("id", "?")
        src, tgt = c.get("source"), c.get("target")
        if not src or not tgt:
            errors.append(f"Edge '{cid}' is missing a source or target endpoint.")
            continue
        if src not in id_set:
            errors.append(f"Edge '{cid}' references a non-existent source '{src}'.")
        if tgt not in id_set:
            errors.append(f"Edge '{cid}' references a non-existent target '{tgt}'.")
        connected.update((src, tgt))
        if "edgeStyle" not in style_of(c):
            warnings.append(f"Edge '{cid}' has no routing style — use edgeStyle=orthogonalEdgeStyle for clean lines.")

    # ---- quality: orphans (ignore containers and title/legend text) ----------------------
    for c in vertices:
        cid = c.get("id", "?")
        if cid not in connected and not is_container(c) and not is_text(c) and edges:
            warnings.append(f"Shape '{cid}' is not connected to anything — is a relationship missing?")

    # ---- quality: overlaps between real shapes (page coordinates) -------------------------
    boxes = []
    for c in vertices:
        if is_container(c) or is_text(c):
            continue
        ab = absolute(c)
        if ab:
            boxes.append((c.get("id", "?"), *ab))
    overlaps = 0
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            _, ax, ay, aw, ah = boxes[i]
            _, bx, by, bw, bh = boxes[j]
            if ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah:
                overlaps += 1
    if overlaps:
        warnings.append(f"{overlaps} pair(s) of shapes overlap — space nodes on a grid (>= {_GAP}px gaps).")

    # ---- professionalism: ONE cloud's icon library, a title, a legend ---------------------
    all_styles = " ".join(style_of(c) for c in cells)
    libs = {
        "aws": bool(re.search(r"mxgraph\.aws|img/lib/aws", all_styles)),
        "azure": bool(re.search(r"mxgraph\.azure|img/lib/azure", all_styles)),
        "gcp": bool(re.search(r"mxgraph\.gcp|img/lib/gcp|mxgraph\.google", all_styles)),
    }
    used = [k for k, v in libs.items() if v]
    diagram_el = root.find(".//diagram")
    name = (diagram_el.get("name") if diagram_el is not None else "") or ""
    multi = bool(re.search(r"multi-?cloud|hybrid", name, re.I))
    declared = (root.get("provider") or "").lower() if root.tag == "mxfile" else ""
    if len(used) > 1 and not multi:
        errors.append(f"Mixed cloud icon libraries ({' + '.join(used)}) — use ONE cloud's icons (the solution's target "
                      f"platform); only a diagram titled multi-cloud/hybrid may mix them.")
    elif declared in libs and used and declared not in used and not multi:
        errors.append(f"The diagram targets {declared} but uses {' + '.join(used)} icons — icons must match the target cloud.")
    has_title = any(c.get("id") == "title" or (is_text(c) and "fontSize=1" in style_of(c)) for c in vertices) or bool(name.strip())
    if not has_title:
        warnings.append("No title — name the diagram (diagram name or a title text cell).")
    if len(vertices) > 8 and not any("legend" in (c.get("id", "") + (c.get("value") or "")).lower() for c in vertices):
        warnings.append("No legend — explain line styles (sync vs async) and the icon set on larger diagrams.")

    stats = {"shapes": len(vertices), "edges": len(edges),
             "containers": sum(1 for c in vertices if is_container(c)),
             "overlaps": overlaps, "cloudLibraries": used}
    return {"ok": not errors, "errors": errors, "warnings": warnings, "stats": stats}


def format_findings(result: dict[str, Any]) -> str:
    """Render a validation result as review-ready markdown."""
    s = result.get("stats", {})
    head = ("✓ **Valid draw.io diagram**" if result["ok"] else "✗ **Invalid draw.io diagram**")
    lines = [f"{head} — {s.get('shapes', 0)} shape(s), {s.get('edges', 0)} edge(s), "
             f"{s.get('containers', 0)} container(s)."]
    for e in result.get("errors", []):
        lines.append(f"- ❌ {e}")
    for w in result.get("warnings", []):
        lines.append(f"- ⚠️ {w}")
    if result["ok"] and not result.get("warnings"):
        lines.append("- No issues found.")
    return "\n".join(lines)
