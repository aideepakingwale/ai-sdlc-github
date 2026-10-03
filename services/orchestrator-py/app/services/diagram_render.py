"""Professional architecture diagrams.

Renders a structured cloud-architecture spec (typed AWS/generic nodes inside
nested VPC/AZ/subnet clusters, with labelled edges) to a self-contained SVG
using the mingrammer `diagrams` library + graphviz — real AWS service icons and
true nested clusters, matching hand-drawn draw.io deployment diagrams.

The spec is what the LLM emits (reliable structured JSON); this module maps it
deterministically to the `diagrams` API — no LLM-authored diagram syntax to get
wrong, and no execution of model-provided code. If `diagrams`/graphviz are not
installed the renderer returns None and the caller keeps the mermaid diagram, so
the feature degrades gracefully.
"""

from __future__ import annotations

import base64
import logging
import re
import tempfile
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("diagrams")


def _node_factory(provider: str) -> Callable[[dict], Any]:
    """Build nodes whose icon comes from the diagram's TARGET CLOUD (cloud_catalog). A service
    with no icon on that cloud becomes a neutral box — never another cloud's icon."""
    from diagrams.custom import Custom
    from diagrams.generic.blank import Blank

    from . import cloud_catalog as cat

    def make(n: dict) -> Any:
        label = str(n.get("label", n["id"]))
        r = cat.resolve(provider, str(n.get("service", "")))
        icon = cat.icon_file(r.icon_path) if r.icon_path else None
        if icon:
            sub = r.service_name if r.service_name and r.service_name.lower() != label.lower() else ""
            return Custom(f"{label}\n{sub}" if sub else label, icon)
        return Blank(label)

    return make


def _svg_with_inlined_icons(svg_path: Path) -> str:
    """graphviz SVG references icon PNGs by file path; inline them as base64 data
    URIs so the SVG is fully self-contained (renders anywhere, offline)."""
    svg = svg_path.read_text(encoding="utf-8")

    def repl(m: re.Match[str]) -> str:
        attr, path = m.group(1), m.group(2)
        try:
            data = Path(path).read_bytes()
            b64 = base64.b64encode(data).decode("ascii")
            return f'{attr}="data:image/png;base64,{b64}"'
        except OSError:
            return m.group(0)

    return re.sub(r'(xlink:href|href)="([^"]+\.png)"', repl, svg)


def render_architecture(spec: dict[str, Any], *, context: list[str] | None = None) -> str | None:
    """Render a cloud-architecture spec to a self-contained SVG string, or None
    if the renderer is unavailable or the spec is empty/invalid."""
    nodes = spec.get("nodes") or []
    if not nodes:
        return None
    try:
        from diagrams import Cluster, Diagram, Edge
    except Exception:  # noqa: BLE001 — diagrams/graphviz not installed
        log.info("diagrams library unavailable; skipping SVG architecture render")
        return None

    try:
        from . import cloud_catalog as cat

        provider = cat.infer_provider(
            spec.get("provider"),
            [spec.get("title", ""), *(context or []), *(c.get("label", "") for c in spec.get("clusters") or [])],
            [n.get("service", "") for n in nodes],
        ).provider
        factory = _node_factory(provider)
        clusters = spec.get("clusters") or []
        edges = spec.get("edges") or []
        by_parent: dict[str, list[dict]] = {}
        for c in clusters:
            by_parent.setdefault(c.get("parent") or "", []).append(c)
        nodes_by_group: dict[str, list[dict]] = {}
        for n in nodes:
            nodes_by_group.setdefault(n.get("group") or "", []).append(n)

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "arch"
            node_objs: dict[str, Any] = {}

            def make_node(n: dict) -> None:
                node_objs[n["id"]] = factory(n)

            def build(parent_id: str) -> None:
                for c in by_parent.get(parent_id, []):
                    with Cluster(c.get("label", c["id"])):
                        for n in nodes_by_group.get(c["id"], []):
                            make_node(n)
                        build(c["id"])

            with Diagram(
                spec.get("title", "Architecture"),
                filename=str(out),
                outformat="svg",
                show=False,
                direction=spec.get("direction", "LR"),
                graph_attr={"splines": "spline", "fontsize": "14", "pad": "0.4", "nodesep": "0.5", "ranksep": "0.7"},
            ):
                for n in nodes_by_group.get("", []):  # top-level (ungrouped) nodes
                    make_node(n)
                build("")  # top-level clusters (parent == "")
                for e in edges:
                    a, b = node_objs.get(e.get("fromId")), node_objs.get(e.get("toId"))
                    if a is not None and b is not None:
                        a >> Edge(label=e.get("label", "")) >> b

            return _svg_with_inlined_icons(Path(f"{out}.svg"))
    except Exception as err:  # noqa: BLE001 — never let a diagram break generation
        log.warning("architecture SVG render failed: %s", err)
        return None
