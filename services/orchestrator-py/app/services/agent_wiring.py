"""Does the pipeline feed its agents? Pure checks over a workflow and the agents attached to it.

An agent reads inputs: the stage brief, the project rules or stack, an approved artefact of a type (`upstream:PRD`), or text a person types.
When a stage runs it by itself, only the first three can be filled. So for each attached agent this says, input by input, where the value comes
from, or that nothing earlier produces it and the agent would be skipped. Output of an earlier stage, and of an agent earlier in the same stage
(agents run in the order they are listed), both count.
"""
from __future__ import annotations

from typing import Any


def _closure(stages: list[dict[str, Any]], idx: int) -> list[dict[str, Any]]:
    """The stages whose output reaches stage `idx`: what it depends on, all the way back; with no dependencies listed, every earlier stage."""
    me = stages[idx]
    by_key = {s["key"]: s for s in stages}
    deps = list(me.get("dependsOn") or [])
    if not deps:
        return [s for s in stages if s["seq"] < me["seq"]]
    seen: list[dict[str, Any]] = []
    todo = list(deps)
    while todo:
        k = todo.pop()
        s = by_key.get(k)
        if s is None or s in seen:
            continue
        seen.append(s)
        todo.extend(s.get("dependsOn") or [])
    return sorted(seen, key=lambda s: s["seq"])


def wire(stages: list[dict[str, Any]], attached: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """`stages`: the workflow's stages (key, seq, name, outputs, dependsOn). `attached[stage_key]`: items in run order, each
    {def_id, name, kind, runs, body}. Returns {stage_key: [{defId, name, blocked, inputs: [{name, source, required, status, from}], outputs: [types]}]}."""
    ordered = sorted(stages, key=lambda s: s["seq"])
    # what each stage's attached agents write, by artefact type
    agent_out: dict[str, dict[str, str]] = {}
    for s in ordered:
        agent_out[s["key"]] = {o["artefact_type"]: it["name"] for it in attached.get(s["key"], []) if it["kind"] == "agent" for o in it["body"].get("outputs", [])}
    result: dict[str, list[dict[str, Any]]] = {}
    for idx, s in enumerate(ordered):
        feeders = _closure(ordered, idx)
        have: dict[str, str] = {}
        for f in feeders:
            for t in f.get("outputs") or []:
                have[str(t).upper()] = f"stage {f['seq']}: {f['name']}"
            for t, who in agent_out[f["key"]].items():
                have.setdefault(t.upper(), f"{who} (stage {f['seq']})")
        items = []
        for it in attached.get(s["key"], []):
            if it["kind"] != "agent":
                continue
            body, ins = it["body"], []
            for i in body.get("inputs", []):
                src, req = i["source"], bool(i.get("required", True))
                if src.startswith("upstream:"):
                    t = src.split(":", 1)[1].upper()
                    origin = have.get(t)
                    ins.append({"name": i["name"], "source": src, "required": req, "status": "ok" if origin else ("missing" if req else "optional"), "from": origin or f"nothing earlier writes {t}"})
                elif src == "user":
                    ins.append({"name": i["name"], "source": src, "required": req, "status": "ok" if it["runs"] == "on_request" else ("missing" if req else "optional"),
                                "from": "typed when you run it" if it["runs"] == "on_request" else "typed by a person, so only when run on request"})
                else:
                    ins.append({"name": i["name"], "source": src, "required": req, "status": "ok", "from": {"brief": "the stage brief", "context:rules": "the project rules", "context:stack": "the stack decision"}.get(src, src)})
            outs = [o["artefact_type"] for o in body.get("outputs", [])]
            blocked = any(x["status"] == "missing" and x["required"] for x in ins)
            items.append({"defId": it["def_id"], "name": it["name"], "runs": it["runs"], "blocked": blocked, "inputs": ins, "outputs": outs})
            for o in outs:           # later agents in this same stage can read it
                have.setdefault(o.upper(), f"{it['name']} (this stage)")
        result[s["key"]] = items
    return result
