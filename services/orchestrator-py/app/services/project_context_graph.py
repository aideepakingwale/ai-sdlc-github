"""The project-level context graph: how context flows through the whole pipeline.

Where the stage manifest answers "what does THIS stage know", this answers "where does knowledge come
from and go across the project": the project's own context (stack, canon, templates, codebase), each
stage with the files attached to it, the artifacts each stage produced, and which later stages build on
them. Pure: ChatService hands it records it already loaded, so it adds no queries of its own.
"""
from __future__ import annotations

from typing import Any

MAX_ARTIFACTS = 150


def _ancestors(stages: list[dict[str, Any]]) -> dict[str, set[str]]:
    by_key = {s["key"]: s for s in stages}
    out: dict[str, set[str]] = {}
    for s in stages:
        seen: set[str] = set()
        todo = list(s.get("dependsOn") or [])
        while todo:
            k = todo.pop()
            if k in seen or k not in by_key or k == s["key"]:
                continue
            seen.add(k)
            todo.extend(by_key[k].get("dependsOn") or [])
        out[s["key"]] = seen
    return out


def build_project_graph(
    *, stages: list[dict[str, Any]], levels: list[list[int]], states: dict[int, str], artifacts: list[dict[str, Any]],
    attachments: dict[int, list[dict[str, Any]]], project: dict[str, Any], manifests: dict[int, dict[str, Any]],
    canon_stages: set[int], template_names: list[str], codebase_files: int,
) -> dict[str, Any]:
    """`artifacts`: latest rows {id, phase, type, title, version}; `attachments`: {phase: [{id, filename}]};
    `manifests`: latest stage manifest by phase (for sizes); `canon_stages`: stages the canon applies to."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    level_of = {seq: li for li, seqs in enumerate(levels) for seq in seqs}
    seq_of = {s["key"]: s["seq"] for s in stages}
    anc = _ancestors(stages)

    stack = str(project.get("tech_stack") or "").strip()
    nodes.append({"id": "project", "kind": "project", "label": project.get("name") or "Project", "level": -1, "tokens": 0,
                  "status": "full", "source": {"type": "project", "stack": stack or "undecided",
                                               "decidedBy": project.get("tech_stack_source") or "", "description": project.get("description") or ""}})
    nodes.append({"id": "stack", "kind": "project", "label": f"Stack: {stack}" if stack else "Stack: undecided", "level": -1, "tokens": 0,
                  "status": "full" if stack else "excluded", "source": {"type": "tech stack", "decidedBy": project.get("tech_stack_source") or ""}})
    shared = ["project", "stack"]
    if canon_stages:
        nodes.append({"id": "canon", "kind": "canon", "label": "Organisation canon", "level": -1, "tokens": 0, "status": "full", "source": {"type": "canon"}})
        shared.append("canon")
    if template_names:
        nodes.append({"id": "templates", "kind": "canon", "label": f"Output templates ({len(template_names)})", "level": -1, "tokens": 0,
                      "status": "full", "source": {"type": "templates", "names": ", ".join(template_names[:12])}})
        shared.append("templates")
    if codebase_files:
        nodes.append({"id": "codebase", "kind": "project", "label": f"Codebase ({codebase_files} files)", "level": -1, "tokens": 0,
                      "status": "full", "source": {"type": "codebase"}})
        shared.append("codebase")

    for s in stages:
        seq = s["seq"]
        m = manifests.get(seq)
        nodes.append({"id": f"stage:{seq}", "kind": "stage", "label": s["name"], "phase": seq, "level": level_of.get(seq, seq - 1),
                      "status": states.get(seq, "NOT_STARTED"), "tokens": (m or {}).get("totals", {}).get("tokens", 0),
                      "source": {"type": "stage", "persona": s.get("persona") or "", "outputs": ", ".join(s.get("outputs") or []),
                                 "ran": bool(m), "contextItems": (m or {}).get("totals", {}).get("items", 0)}})
        for dep in s.get("dependsOn") or []:
            if dep in seq_of:
                edges.append({"id": f"stage:{seq_of[dep]}->stage:{seq}", "from": f"stage:{seq_of[dep]}", "to": f"stage:{seq}", "label": "feeds"})
        for nid in shared:
            if nid == "canon" and seq not in canon_stages:
                continue
            if nid == "codebase" and s["template"] not in (3, 4, 5, 6, 7):
                continue
            edges.append({"id": f"{nid}->stage:{seq}", "from": nid, "to": f"stage:{seq}", "label": "applies to"})
        for a in attachments.get(seq, []):
            nodes.append({"id": f"att:{a['id']}", "kind": "attachment", "label": a["filename"], "phase": seq, "level": level_of.get(seq, seq - 1),
                          "status": "full", "tokens": 0, "source": {"type": "attachment", "filename": a["filename"], "stage": s["name"]}})
            edges.append({"id": f"att:{a['id']}->stage:{seq}", "from": f"att:{a['id']}", "to": f"stage:{seq}", "label": "attached"})

    stage_by_seq = {s["seq"]: s for s in stages}
    shown = artifacts[:MAX_ARTIFACTS]
    for a in shown:
        seq = a["phase"]
        if seq not in stage_by_seq:
            continue
        aid = f"art:{a['id']}"
        nodes.append({"id": aid, "kind": "artifact", "label": f"{a['type']}: {a['title']}", "phase": seq, "level": level_of.get(seq, seq - 1),
                      "status": "full", "tokens": 0, "source": {"type": "artifact", "artifactType": a["type"], "title": a["title"],
                                                                "phase": seq, "version": a.get("version") or 1}})
        edges.append({"id": f"stage:{seq}->{aid}", "from": f"stage:{seq}", "to": aid, "label": "produced"})
        # consumers: a stage uses an upstream artifact when it depends (through any path) on the producer
        producer_key = stage_by_seq[seq]["key"]
        for s in stages:
            if producer_key in anc.get(s["key"], set()):
                edges.append({"id": f"{aid}->stage:{s['seq']}", "from": aid, "to": f"stage:{s['seq']}", "label": "builds on"})

    return {"nodes": nodes, "edges": edges, "levels": len(levels),
            "totals": {"stages": len(stages), "artifacts": len(shown), "attachments": sum(len(v) for v in attachments.values()),
                       "truncated": len(artifacts) > MAX_ARTIFACTS}}
