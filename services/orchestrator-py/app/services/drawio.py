"""Professional draw.io (diagrams.net) architecture diagrams.

The Solution/Technical Architect subagent produces an EDITABLE `.drawio`
(mxGraph XML) diagram — every box, label and connector a real, movable shape
that opens in diagrams.net, the desktop app or the VS Code extension.

Two deterministic (zero-token) tools live here:

  - `cloud_arch_to_drawio(spec)` — convert the CloudArchitecture spec the SA/TA
    agents already emit (nodes / edges / nested clusters) into a well-laid-out
    `.drawio` document with real AWS stencils. Because it reuses the existing
    structured output, every architecture stage gains an editable draw.io at NO
    extra model cost.
  - `validate_drawio(xml)` — a structural + quality validator that returns
    actionable findings, so a hand-authored diagram (the LLM authoring skill) can
    be checked and self-corrected the way OpenAPI is Spectral-linted.

Layout is a simple, robust recursive box-pack: nested clusters (VPC → subnet)
size to fit their children, nodes grid inside, generous non-overlapping spacing.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from html import escape
from typing import Any

# service-key → draw.io AWS4 resource-icon suffix (mxgraph.aws4.resIcon=...).
# Conservative: only well-known stencils; anything else renders as a labelled
# box that still opens and edits cleanly.
_AWS_RESICON: dict[str, str] = {
    "ec2": "ec2", "ecs": "elastic_container_service", "fargate": "fargate",
    "eks": "elastic_kubernetes_service", "lambda": "lambda",
    "alb": "application_load_balancer", "elb": "elastic_load_balancing",
    "nlb": "network_load_balancer", "loadbalancer": "elastic_load_balancing",
    "nat": "nat_gateway", "natgateway": "nat_gateway",
    "cloudfront": "cloudfront", "cdn": "cloudfront",
    "apigateway": "api_gateway", "api": "api_gateway",
    "igw": "internet_gateway", "internetgateway": "internet_gateway",
    "route53": "route_53", "dns": "route_53",
    "rds": "rds", "database": "rds", "db": "rds", "aurora": "aurora",
    "dynamodb": "dynamodb", "elasticache": "elasticache", "cache": "elasticache",
    "s3": "simple_storage_service_s3", "bucket": "simple_storage_service_s3", "storage": "simple_storage_service_s3",
    "sqs": "simple_queue_service_sqs", "queue": "simple_queue_service_sqs",
    "sns": "simple_notification_service_sns", "eventbridge": "eventbridge",
    "waf": "waf", "cognito": "cognito", "secretsmanager": "secrets_manager",
    "vpc": "virtual_private_cloud", "user": "user", "users": "users", "client": "user",
}

_ICON_W, _ICON_H = 78, 78
_BOX_W, _BOX_H = 160, 60
_GAP = 30
_HEADER = 30
_COLS = 3
_MARGIN = 40


def _norm(service: str) -> str:
    return (service or "").strip().lower().replace(" ", "").replace("-", "").replace("_", "")


def _node_size(service: str) -> tuple[int, int]:
    return (_ICON_W, _ICON_H) if _norm(service) in _AWS_RESICON else (_BOX_W, _BOX_H)


def cloud_arch_to_drawio(spec: dict[str, Any], *, title: str | None = None) -> str:
    """Convert a CloudArchitecture spec (dict) into an editable .drawio document.
    Deterministic — no model call. Returns mxGraph XML (`<mxfile>…</mxfile>`)."""
    title = title or spec.get("title") or "Architecture"
    nodes = list(spec.get("nodes") or [])
    edges = list(spec.get("edges") or [])
    clusters = list(spec.get("clusters") or [])

    cluster_ids = {c["id"] for c in clusters}
    # children_of[parent] = child cluster ids; nodes_of[cluster] = its nodes ("" = top level)
    children_of: dict[str, list[str]] = {"": []}
    for c in clusters:
        children_of.setdefault(c["id"], [])
    for c in clusters:
        parent = c.get("parent") or ""
        if parent not in cluster_ids:
            parent = ""
        children_of.setdefault(parent, []).append(c["id"])
    label_of = {c["id"]: c.get("label", c["id"]) for c in clusters}

    nodes_of: dict[str, list[dict]] = {"": []}
    for n in nodes:
        grp = n.get("group") or ""
        if grp not in cluster_ids:
            grp = ""
        nodes_of.setdefault(grp, []).append(n)

    pos: dict[str, tuple[int, int]] = {}          # node id → (x, y)
    box: dict[str, tuple[int, int, int, int]] = {}  # cluster id → (x, y, w, h)

    def place(cid: str, ox: int, oy: int) -> tuple[int, int]:
        header = _HEADER if cid != "" else 0
        inner_x = ox + _GAP
        cursor_y = oy + header + _GAP
        max_right = inner_x
        for child in children_of.get(cid, []):
            cw, ch = place(child, inner_x, cursor_y)
            cursor_y += ch + _GAP
            max_right = max(max_right, inner_x + cw)
        group_nodes = nodes_of.get(cid, [])
        if group_nodes:
            row_h = max(_node_size(n.get("service", "")) [1] for n in group_nodes)
            for i, n in enumerate(group_nodes):
                r, c = divmod(i, _COLS)
                nx = inner_x + c * (_BOX_W + _GAP)
                ny = cursor_y + r * (row_h + _GAP)
                pos[n["id"]] = (nx, ny)
            rows = math.ceil(len(group_nodes) / _COLS)
            cursor_y += rows * (row_h + _GAP)
            max_right = max(max_right, inner_x + min(len(group_nodes), _COLS) * (_BOX_W + _GAP))
        width = max_right - ox
        height = cursor_y - oy
        if cid != "":
            box[cid] = (ox, oy, width, height)
        return width, height

    place("", _MARGIN, _MARGIN)

    cells: list[str] = []

    # Clusters first (behind), shallow → deep so nested boxes sit on top.
    depth = {"": 0}

    def _depth(c: dict) -> int:
        d, p = 1, (c.get("parent") or "")
        seen = set()
        while p and p in cluster_ids and p not in seen:
            seen.add(p)
            d += 1
            p = next((x.get("parent") or "" for x in clusters if x["id"] == p), "")
        return d
    for c in sorted(clusters, key=_depth):
        if c["id"] not in box:
            continue
        x, y, w, h = box[c["id"]]
        style = ("rounded=0;whiteSpace=wrap;html=1;dashed=1;fillColor=none;strokeColor=#7f8c9a;"
                 "verticalAlign=top;fontStyle=1;fontSize=12;fontColor=#5b6478;arcSize=2;")
        cells.append(_vertex(f"c_{_safe(c['id'])}", label_of[c["id"]], style, x, y, w, h))

    # Nodes on top.
    for n in nodes:
        if n["id"] not in pos:
            continue
        x, y = pos[n["id"]]
        svc = _norm(n.get("service", ""))
        w, hgt = _node_size(n.get("service", ""))
        if svc in _AWS_RESICON:
            style = (f"sketch=0;outlineConnect=0;fontColor=#232F3E;gradientColor=none;strokeColor=none;"
                     f"fillColor=#E7157B;dashed=0;verticalLabelPosition=bottom;verticalAlign=top;align=center;"
                     f"html=1;fontSize=11;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.{_AWS_RESICON[svc]};")
        else:
            style = "rounded=1;whiteSpace=wrap;html=1;fillColor=#dae8fc;strokeColor=#6c8ebf;fontSize=12;"
        cells.append(_vertex(f"n_{_safe(n['id'])}", n.get("label", n["id"]), style, x, y, w, hgt))

    # Edges last.
    valid_node_ids = {n["id"] for n in nodes if n["id"] in pos}
    for i, e in enumerate(edges):
        if e.get("fromId") not in valid_node_ids or e.get("toId") not in valid_node_ids:
            continue
        style = "edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=block;strokeColor=#5b6478;fontSize=11;"
        cells.append(
            f'<mxCell id="e_{i}" value="{escape(e.get("label", ""))}" style="{style}" edge="1" parent="1" '
            f'source="n_{_safe(e["fromId"])}" target="n_{_safe(e["toId"])}">'
            f'<mxGeometry relative="1" as="geometry"/></mxCell>'
        )

    body = "".join(cells)
    return (
        '<mxfile host="ai-sdlc">'
        f'<diagram name="{escape(title)}">'
        '<mxGraphModel dx="1024" dy="768" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" '
        'arrows="1" fold="1" page="1" pageScale="1" pageWidth="1600" pageHeight="1100" math="0" shadow="0">'
        '<root><mxCell id="0"/><mxCell id="1" parent="0"/>'
        f'{body}'
        '</root></mxGraphModel></diagram></mxfile>'
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
        style = c.get("style") or ""
        if "edgeStyle" not in style:
            warnings.append(f"Edge '{cid}' has no routing style — use edgeStyle=orthogonalEdgeStyle for clean lines.")

    # Quality warnings.
    is_container = lambda c: (c.get("style") or "").find("container") != -1 or "dashed=1" in (c.get("style") or "")  # noqa: E731
    for c in vertices:
        cid = c.get("id", "?")
        if cid not in connected and not is_container(c) and edges:
            warnings.append(f"Shape '{cid}' is not connected to anything — is a relationship missing?")

    # Overlap detection (top-level, non-container shapes).
    boxes = []
    for c in vertices:
        if is_container(c):
            continue
        geo = c.find("mxGeometry")
        if geo is None:
            continue
        try:
            x, y = float(geo.get("x", 0)), float(geo.get("y", 0))
            w, h = float(geo.get("width", 0)), float(geo.get("height", 0))
        except ValueError:
            continue
        boxes.append((c.get("id", "?"), x, y, w, h))
    overlaps = 0
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            _, ax, ay, aw, ah = boxes[i]
            _, bx, by, bw, bh = boxes[j]
            if ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah:
                overlaps += 1
    if overlaps:
        warnings.append(f"{overlaps} pair(s) of shapes overlap — space nodes on a grid (>= {_GAP}px gaps).")

    stats = {"shapes": len(vertices), "edges": len(edges),
             "containers": sum(1 for c in vertices if is_container(c)),
             "overlaps": overlaps}
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
