"""The plan of one stage run as a graph: who does the work, with what, in which order (docs/plan-graph.md).

`build_graph` turns the stage's plan (deterministic, zero tokens) into typed nodes and edges:

    inputs -> guardrail -> stage agent -> specialist sub-agents (in waves) -> custom agents -> their delegates
           -> artefacts -> validation -> tools -> reviewer gate           (skills hang off the agent that may use them)

`fold_events` lays a run's progress events over the same graph, so one structure serves the plan before the run, the live view while it
runs and the record afterwards. Both are pure functions of their inputs: the service gathers the data, the UI only draws.

Node ids are stable (`sub:<specialist>`, `cust:<definition>`, `tool:<name>`, `art:<TYPE>` ...) because the live events and the stored record
refer to them.
"""
from __future__ import annotations

from typing import Any, Iterable

from .artifact_formats import norm_type

PLANNED, RUNNING, DONE, FAILED, SKIPPED, REUSED, DEFERRED, WAITING, RETRYING = "planned", "running", "done", "failed", "skipped", "reused", "deferred", "waiting", "retrying"
TERMINAL = {DONE, FAILED, SKIPPED, REUSED, DEFERRED}
MAX_LISTED_SKILLS = 6          # more skills than this are drawn as one grouped node, so a stage with many skills stays readable
INPUT_CHARS = 140


def _clip(s: str, n: int = INPUT_CHARS) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _node(nid: str, typ: str, label: str, *, sub: str = "", info: list[tuple[str, str]] | None = None, **extra: Any) -> dict[str, Any]:
    return {"id": nid, "type": typ, "label": label, "sub": sub, "status": PLANNED, "layer": 0,
            "info": [{"k": k, "v": v} for k, v in (info or []) if v not in (None, "")], **extra}


class _G:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, Any]] = []

    def add(self, node: dict[str, Any]) -> dict[str, Any]:
        return self.nodes.setdefault(node["id"], node)

    def link(self, a: str, b: str, kind: str = "flow", label: str = "") -> None:
        if a in self.nodes and b in self.nodes and a != b and not any(e["from"] == a and e["to"] == b for e in self.edges):
            self.edges.append({"id": f"{a}>{b}", "from": a, "to": b, "kind": kind, "label": label})

    def layer_from(self, floor: dict[str, int]) -> int:
        """Longest-path layering: a node sits one column right of everything that feeds it (flow, reads, delegates, writes edges)."""
        layer = {n: floor.get(n, 0) for n in self.nodes}
        for _ in range(len(self.nodes) + 1):
            moved = False
            for e in self.edges:
                if e["kind"] == "uses":            # a skill or a tool an agent may call does not push the agent right
                    continue
                want = layer[e["from"]] + 1
                if layer[e["to"]] < want:
                    layer[e["to"]] = want
                    moved = True
            if not moved:
                break
        for n, v in layer.items():
            self.nodes[n]["layer"] = v
        # a node that is only used (skill / tool) sits beside the agent that uses it
        for e in self.edges:
            if e["kind"] == "uses" and self.nodes[e["to"]]["layer"] <= self.nodes[e["from"]]["layer"]:
                self.nodes[e["to"]]["layer"] = self.nodes[e["from"]]["layer"] + 1
        return max(layer.values(), default=0) + 1


def build_graph(*, stage: dict[str, Any], plan: dict[str, Any], specialists: list[dict[str, Any]], custom: list[dict[str, Any]],
                custom_skills: list[dict[str, Any]], models: dict[str, str]) -> dict[str, Any]:
    """
    stage        {seq, key, name, template, persona, reviewerRole}
    plan         what `ChatService.build_plan` returns (steps, skills, expectedTools, intel, context, overlay, agent)
    specialists  the built-in sub-agents of this stage, each {id, name, role, kind, fields, artifacts, needs, after, canon, stack, attachments, tools}
    custom       attached custom agents, each {def_id, name, version, runs, condition, body, resolved}
    custom_skills attached custom skills, same shape
    models       model role (reason / generate / light) -> the model that role resolves to, for display
    """
    g = _G()
    ctx = plan.get("context") or {}
    intel = plan.get("intel") or {}
    steps = {s.get("id"): s for s in plan.get("steps") or []}
    overlay = plan.get("overlay") or {}
    template = int(stage.get("template") or 0)
    tier = (plan.get("agent") or {}).get("tier") or ""

    # ---- what the stage will produce
    will = [w for w in intel.get("willProduce") or [] if w.get("include", True)]
    outputs = [str(w.get("output") or w.get("type") or "").strip() for w in will] or [str(o) for o in (plan.get("stage") or {}).get("outputs") or []]
    outputs = [o for o in dict.fromkeys(outputs) if o]
    types = {norm_type(o): o for o in outputs}

    # ---- inputs
    instr = (overlay.get("promptOverlay") or "").strip()
    g.add(_node("in:instructions", "input", "Your instructions", sub=_clip(instr, 60) if instr else "none written", info=[("Text", _clip(instr, 400))]))
    prior = ctx.get("priorArtifacts") or []
    if prior:
        by_type = sorted({a["type"] for a in prior})
        g.add(_node("in:upstream", "input", "Earlier stages", sub=f"{len(prior)} artefact(s)", info=[("Artefacts", ", ".join(by_type))]))
    atts = ctx.get("attachments") or []
    if atts:
        g.add(_node("in:attach", "input", "Attached documents", sub=f"{len(atts)} file(s)", info=[("Files", ", ".join(a["filename"] for a in atts[:8]))]))
    if ctx.get("canonApplied"):
        g.add(_node("in:rules", "input", "Project rules", sub="binding on every agent"))
    if any(s.get("stack") for s in specialists):
        g.add(_node("in:stack", "input", "Technology stack", sub="as decided for this project"))

    # ---- guardrail and the stage agent
    g.add(_node("guard", "check", "Input guardrail", sub="checks the request before any model runs"))
    for i in [n for n in g.nodes if n.startswith("in:")]:
        g.link(i, "guard")
    gen = steps.get("generate") or {}
    lead_model = models.get("generate") or _short_model(gen.get("model"))
    g.add(_node("lead", "agent", stage.get("persona") or "Stage agent", sub=f"leads {stage.get('name', 'the stage')}" + (f" · {tier} model" if tier else ""),
                info=[("Stage", stage.get("name", "")), ("Model", lead_model), ("Reviewer", stage.get("reviewerRole", ""))], role="generate", model=lead_model))
    g.link("guard", "lead")

    # ---- built-in specialist sub-agents, in waves
    # The run writes every field of the stage's schema except those the plan leaves out, so the sub-agents are all of the stage's specialists minus the ones
    # whose every deliverable was unticked. (Matching the planner's own wording of the outputs would drop agents whenever a model words them differently.)
    left_out = {norm_type(str(w.get("output") or w.get("type") or "")) for w in intel.get("willProduce") or [] if not w.get("include", True)}
    wanted = [s for s in specialists if not (s["artifacts"] and all(norm_type(a) in left_out for a in s["artifacts"]))] if template != 7 else []
    for s in wanted:
        for a in s["artifacts"]:
            types.setdefault(norm_type(a), a)
    owner_of_field: dict[str, str] = {}
    producer_of_type: dict[str, str] = {}
    for s in wanted:
        nid = f"sub:{s['id']}"
        model = models.get(s["role"], "")
        g.add(_node(nid, "subagent", s["name"], sub=f"writes {', '.join(s['artifacts'][:2])}" + ("…" if len(s["artifacts"]) > 2 else ""),
                    info=[("Writes", ", ".join(s["artifacts"])), ("Kind", s["kind"]), ("Model role", s["role"]), ("Model", model),
                          ("Reads", ", ".join(s["needs"]) if s["needs"] else "only what the stage gives it")], role=s["role"], model=model, fields=list(s["fields"])))
        for f in s["fields"]:
            owner_of_field[f] = nid
        for a in s["artifacts"]:
            producer_of_type[norm_type(a)] = nid
        g.link("lead", nid)
    for s in wanted:                                    # a specialist that reads another's fields runs after it
        for f in s.get("after") or []:
            src = owner_of_field.get(f)
            if src:
                g.link(src, f"sub:{s['id']}", "flow", "reads its output")
        if s["needs"] and "in:upstream" in g.nodes:
            g.link("in:upstream", f"sub:{s['id']}", "reads")
        if s.get("attachments") and "in:attach" in g.nodes:
            g.link("in:attach", f"sub:{s['id']}", "reads")
        if s.get("canon") and "in:rules" in g.nodes:
            g.link("in:rules", f"sub:{s['id']}", "reads")
        if s.get("stack") and "in:stack" in g.nodes:
            g.link("in:stack", f"sub:{s['id']}", "reads")

    # ---- skills (platform skills of this stage, grouped when there are many)
    skills = plan.get("skills") or []
    if skills:
        if len(skills) <= MAX_LISTED_SKILLS:
            for sk in skills:
                g.add(_node(f"skill:{sk['id']}", "skill", sk["name"], sub=f"{sk.get('tier', '')} skill".strip(), info=[("Tier", sk.get("tier", ""))]))
                g.link("lead", f"skill:{sk['id']}", "uses")
        else:
            g.add(_node("skill:*", "skill", f"{len(skills)} skills", sub="available to the stage agent",
                        info=[("Skills", ", ".join(sk["name"] for sk in skills))]))
            g.link("lead", "skill:*", "uses")

    # ---- custom agents attached to the stage, their delegates and custom skills
    custom_ids: list[str] = []
    for it in custom:
        nid = f"cust:{it['def_id']}"
        body = it.get("body") or {}
        outs = body.get("outputs") or []
        role = body.get("role", "generate")
        when = {"always": "every run", "when": f"when {it.get('condition')}", "on_request": "on request only"}.get(it.get("runs"), it.get("runs", ""))
        g.add(_node(nid, "subagent", it["name"], sub=f"custom agent v{it.get('version')} · {when}", custom=True, role=role, model=models.get(role, ""),
                    info=[("Runs", when), ("Writes", ", ".join(o["artefact_type"] for o in outs)), ("Model role", role), ("Model", models.get(role, "")),
                          ("Token cap", f"{body['budget_tokens']:,}" if body.get("budget_tokens") else "platform limit")]))
        custom_ids.append(nid)
        g.link("lead", nid, "flow", "after the stage agents")
        for o in outs:
            producer_of_type.setdefault(norm_type(o["artefact_type"]), nid)
            types.setdefault(norm_type(o["artefact_type"]), o["artefact_type"])
        def add_delegates(parent: str, pbody: dict[str, Any], depth: int) -> None:
            for c in pbody.get("children") or []:
                res = (it.get("resolved") or {}).get(c["agent_id"]) or {}
                cid = f"deleg:{it['def_id']}:{c['agent_id']}"
                cbody = res.get("body") or {}
                g.add(_node(cid, "subagent", res.get("name") or "Delegate", sub="delegate", custom=True, role=cbody.get("role", "generate"),
                            info=[("Called", f"when {c['when']}" if c.get("when") and c["when"] != "always" else "every time"),
                                  ("Writes", ", ".join(o["artefact_type"] for o in cbody.get("outputs") or []))]))
                g.link(parent, cid, "delegates", c["when"] if c.get("when") and c["when"] != "always" else "")
                if depth < 2:
                    add_delegates(cid, cbody, depth + 1)

        add_delegates(nid, body, 1)
    for it in custom_skills:
        sid = f"skill:cust:{it['def_id']}"
        g.add(_node(sid, "skill", it["name"], sub=f"custom skill v{it.get('version')} · on request", custom=True))
        g.link("lead", sid, "uses")

    # ---- artefacts
    for t, label in types.items():
        aid = f"art:{t}"
        g.add(_node(aid, "artefact", label, sub="deliverable", info=[("Written by", g.nodes[producer_of_type[t]]["label"] if t in producer_of_type else (stage.get("persona") or "the stage agent"))]))
        g.link(producer_of_type.get(t, "lead"), aid, "writes")

    # ---- validation, tools, gate
    if "validate" in steps:
        v = steps["validate"]
        g.add(_node("validate", "check", "Validate output", sub="against your intent and for syntax", role="light", model=models.get("light") or _short_model(v.get("model")),
                    info=[("Model", models.get("light") or _short_model(v.get("model")))]))
        for t in types:
            g.link(f"art:{t}", "validate")
        if not types:
            g.link("lead", "validate")
    g.add(_node("gate", "gate", f"{stage.get('reviewerRole', 'Reviewer')} sign-off", sub="human review gate"))
    tools_declared: dict[str, dict[str, Any]] = {}
    for s in wanted:
        for t in s.get("tools") or []:
            tools_declared.setdefault(t["name"], {"by": []})["by"].append((s["id"], t))
    for sid, st in steps.items():
        if st.get("kind") == "tool":
            name = st["tools"][0] if st.get("tools") else st["label"]
            deferred = bool(st.get("deferred"))
            g.add(_node(f"tool:{name}", "tool", name, sub="published when approved" if deferred else "runs after generation", deferred=deferred,
                        info=[("When", "after the gate approves (external write)" if deferred else "after generation")]))
            g.link("validate" if "validate" in g.nodes else "lead", f"tool:{name}")
            g.link(f"tool:{name}", "gate")
    available = set(plan.get("expectedTools") or [])
    for name, d in tools_declared.items():
        ok = name in available or f"tool:{name}" in g.nodes
        g.add(_node(f"tool:{name}", "tool", name, sub="used by a sub-agent" if ok else "not connected here: skipped", unavailable=not ok,
                    info=[("Status", "connected" if ok else "this integration is not configured, so the step is skipped")]))
        for sid_, t in d["by"]:
            g.link(f"sub:{sid_}", f"tool:{name}", "uses", t.get("access", ""))
        g.link(f"tool:{name}", "gate")
    g.link("validate" if "validate" in g.nodes else "lead", "gate")
    for nid in custom_ids:
        if not any(e["from"] == nid for e in g.edges if e["kind"] in ("flow", "writes")):
            g.link(nid, "gate")

    g.layer_from({})
    gate_layer = max(n["layer"] for n in g.nodes.values() if n["id"] != "gate") + 1
    g.nodes["gate"]["layer"] = gate_layer
    layers = gate_layer + 1
    counts = {"agents": 1 + sum(1 for n in g.nodes.values() if n["type"] == "subagent"),
              "subAgents": sum(1 for n in g.nodes.values() if n["type"] == "subagent"),
              "skills": len(skills) + len(custom_skills), "tools": sum(1 for n in g.nodes.values() if n["type"] == "tool"),
              "artefacts": sum(1 for n in g.nodes.values() if n["type"] == "artefact")}
    return {
        "version": 1,
        "stage": {k: stage.get(k) for k in ("seq", "key", "name", "template", "persona", "reviewerRole")},
        "nodes": list(g.nodes.values()), "edges": g.edges, "layers": layers, "counts": counts,
        "fieldOwners": owner_of_field,
        "run": {"state": "planned", "jobId": None, "startedAt": None, "finishedAt": None},
    }


def _short_model(m: Any) -> str:
    """A model for display. The plan resolves a step's model to {provider, model, ...}; a route gives 'provider/model'."""
    if isinstance(m, dict):
        m = m.get("model") or ""
    return str(m or "").split("/")[-1] if m else ""


# ------------------------------------------------------------------------------------------------ a run, laid over the plan
def fold_events(graph: dict[str, Any], events: Iterable[dict[str, Any]], *, run: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a copy of `graph` with every node's status set from the run's events. Unknown events are ignored; a tool or a custom agent
    that ran without being planned is added (marked `unplanned`), so the record shows what really ran, not only what was intended."""
    out = {**graph, "nodes": [dict(n) for n in graph["nodes"]], "edges": [dict(e) for e in graph["edges"]], "run": {**graph.get("run", {}), **(run or {})}}
    by_id = {n["id"]: n for n in out["nodes"]}
    owners = graph.get("fieldOwners") or {}
    started = False
    parts: dict[str, dict[str, str]] = {}        # specialist node -> field -> status

    def setn(nid: str, status: str, detail: str = "") -> None:
        n = by_id.get(nid)
        if n is None:
            return
        if status == RETRYING and n["status"] != RETRYING:
            n["retries"] = int(n.get("retries") or 0) + 1                    # one more attempt (not one per event of the same attempt)
        if status == RUNNING and n["status"] == RETRYING:
            status = RETRYING                                                # still on the retry until it finishes or fails
        n["status"] = status
        if detail:
            n["detail"] = detail

    def ensure_after(nid: str, typ: str, label: str, after: str, **extra: Any) -> None:
        if nid in by_id:
            return
        n = _node(nid, typ, label, sub="not in the plan", unplanned=True, **extra)
        n["layer"] = by_id[after]["layer"] + 1 if after in by_id else 0
        out["nodes"].append(n)
        by_id[nid] = n
        if after in by_id:
            out["edges"].append({"id": f"{after}>{nid}", "from": after, "to": nid, "kind": "flow", "label": ""})

    for ev in events:
        t = ev.get("type")
        if t == "session" and not started:
            started = True
            for i in [n["id"] for n in out["nodes"] if n["type"] == "input"]:
                setn(i, DONE)
            setn("guard", RUNNING)
        elif t == "node":
            name = ev.get("node")
            label = str(ev.get("label") or "")
            if name == "guardrail" and not ev.get("status"):
                setn("guard", DONE)
                if by_id["lead"]["status"] == PLANNED:
                    setn("lead", RUNNING)
            elif name in ("executor", "agent", "planner", "compressor"):
                setn("guard", DONE)
                if by_id["lead"]["status"] in (PLANNED, WAITING):
                    setn("lead", RUNNING)
            elif name == "validator":
                if "validated" in label:
                    setn("validate", DONE, label)
                elif "remain after rework" in label:
                    setn("validate", FAILED, label)
                else:
                    setn("validate", RUNNING, label)
        elif t == "part":
            field, st = str(ev.get("part") or ""), str(ev.get("status") or "")
            nid = owners.get(field)
            if nid:
                parts.setdefault(nid, {})[field] = st
                states = set(parts[nid].values())
                setn(nid, FAILED if FAILED in states else RUNNING if RUNNING in states else DONE)
            elif field and by_id["lead"]["status"] in (PLANNED, RUNNING):
                setn("lead", RUNNING)
            if st == "failed" and ev.get("error") and nid:
                by_id[nid]["detail"] = _clip(str(ev["error"]), 240)
        elif t == "step":
            nid, st = str(ev.get("id") or ""), str(ev.get("status") or "")
            if not nid and ev.get("field"):
                nid = owners.get(str(ev["field"]), "lead")                      # a step named by the artefact field it writes
            if st == RETRYING:
                setn(nid, RETRYING, str(ev.get("detail") or ""))
                continue
            if nid not in by_id and nid.startswith("sub:"):
                ensure_after(nid, "subagent", str(ev.get("name") or nid[4:]), "lead")
            if nid not in by_id and nid.startswith("cust:"):
                ensure_after(nid, "subagent", str(ev.get("name") or "Custom agent"), "lead", custom=True)
            if nid not in by_id and nid.startswith("deleg:"):
                parent = "cust:" + nid.split(":")[1]
                ensure_after(nid, "subagent", str(ev.get("name") or "Delegate"), parent, custom=True)
            if st in (RUNNING, DONE, FAILED, SKIPPED, REUSED):
                setn(nid, st, str(ev.get("detail") or ""))
            if nid in by_id:
                if ev.get("model"):
                    by_id[nid]["model"] = ev["model"]
                if ev.get("tokens") is not None:
                    by_id[nid]["tokens"] = ev["tokens"]
        elif t == "tool_call":
            name = str(ev.get("tool") or "")
            nid = f"tool:{name}"
            ensure_after(nid, "tool", name, "validate" if "validate" in by_id else "lead")
            setn(nid, {"start": RUNNING, "success": DONE, "error": FAILED, "skipped": SKIPPED, "deferred": DEFERRED}.get(str(ev.get("status")), RUNNING),
                 _clip(str(ev.get("summary") or ""), 240))
        elif t == "artifact":
            a = ev.get("artifact") or {}
            nid = f"art:{norm_type(str(a.get('type') or ''))}"
            if nid in by_id:
                setn(nid, DONE)
        elif t == "gate":
            setn("gate", WAITING, str(ev.get("status") or ""))
        elif t == "done":
            setn("lead", DONE)
            for n in out["nodes"]:
                if n["status"] in (RUNNING, RETRYING):
                    n["status"] = DONE
            if by_id["gate"]["status"] == PLANNED:
                setn("gate", WAITING)
        elif t == "error":
            setn("lead", FAILED, _clip(str(ev.get("message") or ""), 240))
            out["run"]["error"] = _clip(str(ev.get("message") or ""), 240)
    # a deliverable is done once its writer is; failed when its writer failed
    for n in out["nodes"]:
        if n["type"] != "artefact" or n["status"] != PLANNED:
            continue
        writers = [e["from"] for e in out["edges"] if e["to"] == n["id"] and e["kind"] == "writes"]
        sts = {by_id[w]["status"] for w in writers if w in by_id}
        if sts and sts <= {DONE, REUSED}:
            n["status"] = DONE
        elif FAILED in sts:
            n["status"] = FAILED
    return out


# ------------------------------------------------------------------------------------------------ a run that left only what it saved
def reconstruct(graph: dict[str, Any], *, parts: list[dict[str, Any]], runs: list[dict[str, Any]], validations: list[dict[str, Any]], stage_status: str) -> dict[str, Any] | None:
    """Rebuild what a finished run did from what it saved (the stage's parts, the record of each artefact, the validation audit events), for a run
    whose live events are gone and which stored no graph (it ran before this feature, or its record could not be written). Returns None when the
    stage left nothing behind. Honest about the gaps: tools that are not recorded stay planned, and a step with no output is marked skipped."""
    if not parts and not runs:
        return None
    out = {**graph, "nodes": [dict(n) for n in graph["nodes"]], "edges": [dict(e) for e in graph["edges"]], "run": {**graph.get("run", {}), "source": "reconstructed"}}
    by_id = {n["id"]: n for n in out["nodes"]}
    owners = graph.get("fieldOwners") or {}

    def put(nid: str, status: str, detail: str = "", **extra: Any) -> None:
        n = by_id.get(nid)
        if n is None:
            return
        n["status"] = status
        if detail:
            n["detail"] = detail
        for k, v in extra.items():
            if v not in (None, "", 0):
                n[k] = v

    def ensure(nid: str, typ: str, label: str, after: str) -> None:
        if nid in by_id:
            return
        n = _node(nid, typ, label, sub="not in the plan", unplanned=True)
        n["layer"] = by_id[after]["layer"] + 1 if after in by_id else 0
        out["nodes"].append(n)
        by_id[nid] = n
        if after in by_id:
            out["edges"].append({"id": f"{after}>{nid}", "from": after, "to": nid, "kind": "flow", "label": ""})

    def tok(r: dict[str, Any]) -> int | None:
        t = r.get("tokens")
        return (int((t or {}).get("prompt") or 0) + int((t or {}).get("completion") or 0)) if isinstance(t, dict) else None

    ok_parts = [p for p in parts if p.get("status") == "done"]
    per_node: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}
    for p in parts:
        field, st = str(p.get("field") or ""), str(p.get("status") or "")
        run = p.get("run") if isinstance(p.get("run"), dict) else {}
        status = FAILED if st in ("failed", "running") else REUSED if run.get("reused") else DONE
        detail = str(p.get("error") or "") or ("it did not finish" if st == "running" else "")
        per_node.setdefault(owners.get(field) or "lead", []).append((status, detail, run))
    for nid, items in per_node.items():                  # a sub-agent that writes several fields is as bad as its worst field
        states = [i[0] for i in items]
        status = FAILED if FAILED in states else DONE if DONE in states else REUSED
        if nid == "lead" and status != FAILED:
            continue
        detail = next((i[1] for i in items if i[0] == FAILED and i[1]), "")
        runs_ = [i[2] for i in items if i[2]]
        put(nid, status, _clip(detail, 240), model=(runs_[0].get("model") if runs_ else None), tokens=sum(tok(r) or 0 for r in runs_))
    evidence = bool(ok_parts or runs)
    if evidence:
        for i in [n["id"] for n in out["nodes"] if n["type"] == "input"] + ["guard"]:
            put(i, DONE)
    failed_all = bool(parts) and all(p.get("status") != "done" for p in parts) and not runs
    put("lead", FAILED if failed_all else DONE if evidence else by_id["lead"]["status"])
    # custom agents and their delegates, from the record of what they wrote
    seen_custom: set[str] = set()
    for r in runs:
        rec = r.get("run") if isinstance(r.get("run"), dict) else {}
        if not rec.get("custom"):
            continue
        aid = str(r.get("agent_id") or rec.get("agentId") or "")
        nid = f"cust:{aid}"
        ensure(nid, "subagent", str(r.get("agent_name") or rec.get("agentName") or "Custom agent"), "lead")
        put(nid, DONE, model=rec.get("model"), tokens=tok(rec))
        seen_custom.add(nid)
        for cid in rec.get("delegates") or []:
            did = f"deleg:{aid}:{cid}"
            if did in by_id:
                put(did, DONE)
    if evidence:
        for n in out["nodes"]:
            if n["id"].startswith("cust:") and n["id"] not in seen_custom and n["status"] == PLANNED and "on request" not in n["sub"]:
                put(n["id"], SKIPPED, "it saved no output for this run (its condition was not met, it needed an input the stage did not have, or it failed)")
    # deliverables that exist
    have = {norm_type(str(r.get("type") or "")) for r in runs}
    for n in out["nodes"]:
        if n["type"] == "artefact" and n["id"][4:] in have and n["status"] == PLANNED:
            n["status"] = DONE
    # validation
    if validations:
        retries = sum(1 for v in validations if int(v.get("attempt") or 0) > 0)
        last = validations[-1]
        errors = last.get("errors") or []
        if last.get("ok") or not errors:
            put("validate", DONE)
        else:
            put("validate", FAILED, f"{len(errors)} issue(s) remained after rework and were flagged for the reviewer")
        if retries:
            by_id["validate"]["retries"] = retries
    # the gate follows the stage's decision
    gate_status = {"PENDING_REVIEW": (WAITING, ""), "APPROVED": (DONE, ""), "ESCALATED": (FAILED, "escalated"), "AMEND_REQUESTED": (FAILED, "changes were requested")}.get(stage_status)
    if gate_status and evidence:
        put("gate", gate_status[0], gate_status[1])
    for n in out["nodes"]:
        if n["type"] == "tool" and evidence and n["status"] == PLANNED:
            if n.get("deferred"):
                put(n["id"], DONE if stage_status == "APPROVED" else DEFERRED)
            else:
                n["detail"] = "Whether this tool ran was not recorded for this run"
    for n in out["nodes"]:                              # deliverables follow their writers
        if n["type"] == "artefact" and n["status"] == PLANNED:
            writers = [e["from"] for e in out["edges"] if e["to"] == n["id"] and e["kind"] == "writes"]
            sts = {by_id[w]["status"] for w in writers if w in by_id}
            if FAILED in sts:
                n["status"] = FAILED
    out["run"]["state"] = "failed" if failed_all else "done"
    return out
