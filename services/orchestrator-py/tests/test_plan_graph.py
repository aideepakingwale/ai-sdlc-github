"""The stage plan as a graph (docs/plan-graph.md): structure of the plan, the live fold of a run's events, and the views of the service."""
from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

import pytest

from app.agents import specialists
from app.services import agent_catalog
from app.services.artifact_formats import norm_type
from app.services.plan_graph import build_graph, fold_events
from app.services.plan_graph_service import PlanGraphService
from app.services.plan_model import derive_plan_steps

TEMPLATE = 2
FLOW = {"flow", "writes", "delegates", "reads"}


def specs_of(template: int = TEMPLATE) -> list[dict[str, Any]]:
    out = []
    for s in specialists.BY_TEMPLATE.get(template, []):
        cat = agent_catalog.get(s.id) or {}
        out.append({"id": s.id, "name": s.name, "role": s.role, "kind": s.kind, "fields": list(s.fields), "artifacts": list(s.artifacts), "needs": list(s.needs),
                    "after": list(s.after), "canon": s.canon, "stack": s.stack, "attachments": s.attachments, "tools": list(cat.get("tools") or [])})
    return out


def plan_for(specs: list[dict[str, Any]], *, skills: int = 2, tools: tuple[str, ...] = ("github_commit_diagrams", "confluence_publish_hld")) -> dict[str, Any]:
    outputs = [s["artifacts"][0] for s in specs if s["artifacts"]][:3]
    sk = [{"id": f"sk{i}", "name": f"Skill {i}", "tier": "local"} for i in range(skills)]
    steps = derive_plan_steps(template=TEMPLATE, roster={"providers": []}, step_overrides={}, outputs=outputs, skills=sk, tools=list(tools),
                              external_write_tools={"confluence_publish_hld"}, gen_prompt_tokens=1000)
    return {"status": "NOT_STARTED", "canEdit": True, "steps": steps, "skills": sk, "expectedTools": list(tools), "agent": {"persona": "Architect", "tier": "frontier"},
            "intel": {"willProduce": [{"output": o, "include": True} for o in outputs]}, "stage": {"outputs": outputs}, "catalog": {"recommended": "bedrock/claude"},
            "context": {"priorArtifacts": [{"id": "a1", "phase": 1, "type": "PRD", "title": "PRD"}], "attachments": [{"id": "f1", "filename": "brief.pdf"}], "canonApplied": True},
            "overlay": {"promptOverlay": "Use event sourcing for payments."}}


STAGE = {"seq": 2, "key": "hld", "name": "High-level design", "template": TEMPLATE, "persona": "Solution Architect", "reviewerRole": "SA"}
MODELS = {"reason": "m-reason", "generate": "m-gen", "light": "m-light"}


def custom_agent(def_id="c1", name="Risk scorer", runs="always", children=()):
    body = {"role": "reason", "outputs": [{"name": "score", "artefact_type": "RISK_ASSESSMENT", "format": "JSON"}], "children": list(children), "budget_tokens": 20000}
    return {"def_id": def_id, "name": name, "version": 3, "runs": runs, "condition": "", "body": body, "resolved": {}}


def graph(**kw: Any) -> dict[str, Any]:
    specs = kw.pop("specs", specs_of())
    return build_graph(stage=STAGE, plan=kw.pop("plan", None) or plan_for(specs), specialists=specs, custom=kw.pop("custom", []),
                       custom_skills=kw.pop("custom_skills", []), models=MODELS)


def by_id(g: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {n["id"]: n for n in g["nodes"]}


# ----------------------------------------------------------------------------- the plan
def test_the_plan_graph_has_every_kind_of_part_and_is_a_clean_left_to_right_flow():
    g = graph()
    nodes = by_id(g)
    kinds = {n["type"] for n in g["nodes"]}
    assert {"input", "check", "agent", "subagent", "skill", "tool", "artefact", "gate"} <= kinds
    assert nodes["lead"]["label"] == "Solution Architect" and nodes["lead"]["model"] == "m-gen"
    for e in g["edges"]:
        assert e["from"] in nodes and e["to"] in nodes
        if e["kind"] in FLOW:
            assert nodes[e["from"]]["layer"] < nodes[e["to"]]["layer"], e          # nothing flows backwards or sideways
    assert nodes["gate"]["layer"] == max(n["layer"] for n in g["nodes"]) == g["layers"] - 1
    assert g["counts"]["subAgents"] == sum(1 for n in g["nodes"] if n["type"] == "subagent") > 0
    assert all(n["status"] == "planned" for n in g["nodes"]) and g["run"]["state"] == "planned"


def test_each_planned_deliverable_comes_from_its_specialist_and_is_checked_before_the_gate():
    specs = specs_of()
    g = graph(specs=specs)
    nodes, edges = by_id(g), g["edges"]
    first = specs[0]
    art = f"art:{norm_type(first['artifacts'][0])}"
    assert art in nodes and any(e["from"] == f"sub:{first['id']}" and e["to"] == art and e["kind"] == "writes" for e in edges)
    assert any(e["from"] == art and e["to"] == "validate" for e in edges) and any(e["from"] == "validate" and e["to"] == "gate" for e in edges)
    assert nodes["tool:confluence_publish_hld"]["deferred"] is True and nodes["tool:github_commit_diagrams"]["deferred"] is False


def test_a_specialist_that_reads_another_runs_after_it_and_inputs_feed_only_those_that_declare_them():
    specs = specs_of()
    g = graph(specs=specs)
    nodes = by_id(g)
    for s in specs:
        nid = f"sub:{s['id']}"
        if nid not in nodes:
            continue
        for f in s["after"]:
            owner = g["fieldOwners"].get(f)
            if owner:
                assert nodes[owner]["layer"] < nodes[nid]["layer"]
        reads = {e["from"] for e in g["edges"] if e["to"] == nid and e["kind"] == "reads"}
        assert ("in:upstream" in reads) == bool(s["needs"])


def test_custom_agents_come_after_the_stage_agents_with_their_delegates_skills_and_deliverables():
    child = {"agent_id": "c2", "when": "score > 0.7"}
    ca = custom_agent(children=[child])
    ca["resolved"] = {"c2": {"name": "Fraud analyst", "body": {"role": "reason", "outputs": [], "children": [{"agent_id": "c3", "when": "always"}]}},
                      "c3": {"name": "Escalation writer", "body": {"role": "generate", "outputs": []}}}
    g = graph(custom=[ca], custom_skills=[{"def_id": "s1", "name": "Redact", "version": 1}])
    nodes = by_id(g)
    assert nodes["cust:c1"]["custom"] is True and "v3" in nodes["cust:c1"]["sub"]
    assert nodes["deleg:c1:c2"]["label"] == "Fraud analyst" and nodes["deleg:c1:c3"]["label"] == "Escalation writer"
    assert any(e["from"] == "cust:c1" and e["to"] == "deleg:c1:c2" and e["kind"] == "delegates" and e["label"] == "score > 0.7" for e in g["edges"])
    assert any(e["from"] == "deleg:c1:c2" and e["to"] == "deleg:c1:c3" for e in g["edges"])
    assert nodes["cust:c1"]["layer"] < nodes["deleg:c1:c2"]["layer"] < nodes["deleg:c1:c3"]["layer"]
    assert nodes["art:RISK_ASSESSMENT"] and nodes["skill:cust:s1"]["custom"] is True
    on_request = graph(custom=[custom_agent(runs="on_request")])
    assert "on request only" in by_id(on_request)["cust:c1"]["sub"]


def test_many_skills_are_grouped_into_one_node_so_the_graph_stays_readable():
    specs = specs_of()
    g = graph(specs=specs, plan=plan_for(specs, skills=9))
    skills = [n for n in g["nodes"] if n["type"] == "skill"]
    assert len(skills) == 1 and skills[0]["label"] == "9 skills" and g["counts"]["skills"] == 9


def test_a_stage_with_nothing_planned_still_has_a_complete_graph():
    g = graph(specs=[], plan={"steps": [], "intel": {}, "context": {}, "overlay": {}, "stage": {"outputs": []}})
    nodes = by_id(g)
    assert {"in:instructions", "guard", "lead", "gate"} <= set(nodes) and all(e["from"] in nodes and e["to"] in nodes for e in g["edges"])
    assert any(e["from"] == "lead" and e["to"] == "gate" for e in g["edges"])


# ----------------------------------------------------------------------------- a run laid over the plan
def run_events(g: dict[str, Any]) -> tuple[str, str, str]:
    owners = g["fieldOwners"]
    field = next(iter(owners))
    return field, owners[field], f"art:{norm_type(next(n['label'] for n in g['nodes'] if n['id'].startswith('art:')))}"


def test_events_move_nodes_through_running_done_and_failed_without_touching_the_plan():
    g = graph()
    before = copy.deepcopy(g)
    field, owner, _ = run_events(g)
    others = [f for f, o in g["fieldOwners"].items() if o != owner]
    ev = [{"type": "session"}, {"type": "node", "node": "guardrail", "label": "Input guardrail"}, {"type": "part", "part": field, "status": "running"}]
    mid = by_id(fold_events(g, ev))
    assert mid["guard"]["status"] == "done" and mid["lead"]["status"] == "running" and mid[owner]["status"] == "running"
    done = by_id(fold_events(g, ev + [{"type": "part", "part": field, "status": "done"}, {"type": "node", "node": "validator", "label": "Validator: output validated ✓"},
                                      {"type": "tool_call", "tool": "github_commit_diagrams", "status": "success", "summary": "committed"},
                                      {"type": "tool_call", "tool": "confluence_publish_hld", "status": "deferred"}, {"type": "gate", "status": "PENDING_REVIEW"},
                                      {"type": "done"}]))
    assert done[owner]["status"] == "done" and done["validate"]["status"] == "done" and done["lead"]["status"] == "done"
    assert done["tool:github_commit_diagrams"]["status"] == "done" and done["tool:confluence_publish_hld"]["status"] == "deferred" and done["gate"]["status"] == "waiting"
    failed = by_id(fold_events(g, ev + [{"type": "part", "part": field, "status": "failed", "error": "model said no"}]))
    assert failed[owner]["status"] == "failed" and "model said no" in failed[owner]["detail"]
    assert g == before and others is not None                                      # folding never edits the plan it was given


def test_a_deliverable_is_done_when_its_writer_is_and_failed_when_it_failed():
    g = graph()
    field, owner, art = run_events(g)
    ok = by_id(fold_events(g, [{"type": "part", "part": field, "status": "done"}]))
    assert ok[art]["status"] == "done"
    bad = by_id(fold_events(g, [{"type": "part", "part": field, "status": "failed", "error": "x"}]))
    assert bad[art]["status"] == "failed"


def test_custom_agents_and_their_delegates_report_through_step_events_and_unplanned_work_is_shown_as_such():
    ca = custom_agent(children=[{"agent_id": "c2", "when": "always"}])
    ca["resolved"] = {"c2": {"name": "Fraud analyst", "body": {"role": "reason", "outputs": []}}}
    g = graph(custom=[ca])
    n = by_id(fold_events(g, [{"type": "step", "id": "cust:c1", "status": "running"}, {"type": "step", "id": "cust:c1", "status": "done", "model": "m-r", "tokens": 1200},
                              {"type": "step", "id": "deleg:c1:c2", "status": "skipped", "detail": "its condition was not met"},
                              {"type": "step", "id": "cust:zzz", "name": "Surprise agent", "status": "done"},
                              {"type": "tool_call", "tool": "trivy_scan_image", "status": "error", "summary": "boom"}]))
    assert n["cust:c1"]["status"] == "done" and n["cust:c1"]["tokens"] == 1200 and n["cust:c1"]["model"] == "m-r"
    assert n["deleg:c1:c2"]["status"] == "skipped" and n["deleg:c1:c2"]["detail"] == "its condition was not met"
    assert n["cust:zzz"]["unplanned"] is True and n["cust:zzz"]["label"] == "Surprise agent"
    assert n["tool:trivy_scan_image"]["unplanned"] is True and n["tool:trivy_scan_image"]["status"] == "failed"


def test_a_failed_run_marks_the_stage_agent_failed_and_unknown_events_are_ignored():
    g = graph()
    out = fold_events(g, [{"type": "session"}, {"type": "nonsense", "x": 1}, {"type": "content_delta", "text": "hi"}, {"type": "error", "code": "X", "message": "gateway down"}])
    n = by_id(out)
    assert n["lead"]["status"] == "failed" and "gateway down" in n["lead"]["detail"] and out["run"]["error"] == "gateway down"


# ----------------------------------------------------------------------------- the service
class FakeChat:
    agent_defs = None

    def __init__(self, status: str) -> None:
        self.status = status

    async def build_plan(self, **_k: Any) -> dict[str, Any]:
        return {**plan_for(specs_of()), "status": self.status}

    async def _stage_for(self, *_a: Any) -> tuple[dict, dict]:
        return {}, {**STAGE, "outputs": []}

    async def _custom_agents_for(self, *_a: Any) -> list:
        return []


class FakeDb:
    def __init__(self, stored: dict | None = None) -> None:
        self.stored, self.saved = stored, []

    async def latest_generation_job(self, *_a: Any) -> dict:
        return {"id": "job-9", "status": "done"}

    async def get_run_graph(self, *_a: Any, **_k: Any) -> dict | None:
        return self.stored

    async def save_run_graph(self, job_id: str, project_id: str, phase: int, status: str, graph: dict) -> None:
        self.saved.append((job_id, status, graph))


class FakeRedis:
    def __init__(self, events: list[bytes]) -> None:
        self.events = events

    async def lrange(self, *_a: Any) -> list[bytes]:
        return self.events


def service(status: str, *, active: bool, stored: bool, events: list[bytes] | None = None) -> tuple[PlanGraphService, FakeDb]:
    base = graph()
    db = FakeDb({"job_id": "job-8", "status": "done", "graph": fold_events(base, [{"type": "done"}])} if stored else None)
    svc = PlanGraphService(FakeChat(status), db, FakeRedis(events or []), SimpleNamespace(route_chain=lambda r: _chain(r)))
    jobs = SimpleNamespace(is_active=lambda *_a: _async(active), _progress_key=lambda p, n: f"progress:{p}:{n}")
    svc.jobs = jobs
    return svc, db


async def _chain(role: str) -> list[str]:
    return [f"model-for-{role}"]


async def _async(v: Any) -> Any:
    return v


USER = SimpleNamespace(id="u1")


async def test_the_view_is_live_while_a_run_is_active_and_follows_its_events():
    svc, _ = service("IN_PROGRESS", active=True, stored=True, events=[b'{"type":"session"}', b'{"type":"node","node":"guardrail","label":"Input guardrail"}'])
    v = await svc.view("p", 2, USER)
    n = by_id(v["graph"])
    assert v["mode"] == "live" and n["guard"]["status"] == "done" and n["lead"]["status"] == "running" and v["graph"]["run"]["jobId"] == "job-9"
    assert n["lead"]["model"] == "model-for-generate"


async def test_after_a_run_the_stage_waiting_on_review_shows_what_ran_and_a_stage_to_re_plan_shows_the_plan():
    waiting, _ = service("PENDING_REVIEW", active=False, stored=True)
    v = await waiting.view("p", 2, USER)
    assert v["mode"] == "record" and v["graph"]["run"]["jobId"] == "job-8" and v["available"] == {"planned": True, "run": True}
    amend, _ = service("AMEND_REQUESTED", active=False, stored=True)
    assert (await amend.view("p", 2, USER))["mode"] == "planned"
    assert (await amend.view("p", 2, USER, "run"))["mode"] == "record"                  # the person can still ask for the last run
    assert (await waiting.view("p", 2, USER, "planned"))["mode"] == "planned"
    fresh, _ = service("NOT_STARTED", active=False, stored=False)
    f = await fresh.view("p", 2, USER, "run")
    assert f["mode"] == "planned" and f["available"]["run"] is False


async def test_the_view_rejects_an_unknown_view_name():
    from app.domain.errors import SdlcError

    svc, _ = service("NOT_STARTED", active=False, stored=False)
    with pytest.raises(SdlcError):
        await svc.view("p", 2, USER, "everything")


async def test_the_record_is_stored_when_a_run_ends_and_a_failure_to_store_never_raises():
    svc, db = service("PENDING_REVIEW", active=False, stored=False, events=[b'{"type":"session"}', b'{"type":"done"}'])
    await svc.record("p", 2, "job-9", "done", USER)
    (job, status, g), = db.saved
    assert (job, status) == ("job-9", "done") and by_id(g)["lead"]["status"] == "done" and g["run"] == {**g["run"], "jobId": "job-9", "state": "done"}

    async def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("db down")

    db.save_run_graph = boom                                                            # type: ignore[method-assign]
    await svc.record("p", 2, "job-10", "done", USER)


def test_a_tool_a_sub_agent_declares_but_the_project_has_not_connected_is_shown_as_unavailable():
    specs = specs_of()
    plan = plan_for(specs, tools=())
    plan["expectedTools"] = []
    shown = {k: n for k, n in by_id(graph(specs=specs, plan=plan)).items() if n["type"] == "tool"}
    assert shown, "the built-in specialists of this stage declare tools"
    assert all(n.get("unavailable") for n in shown.values())
    name = next(iter(shown)).split(":", 1)[1]
    plan["expectedTools"] = [name]
    assert not by_id(graph(specs=specs, plan=plan))[f"tool:{name}"].get("unavailable")


def test_the_model_shown_for_a_step_is_its_name_whether_the_plan_gives_an_object_or_a_route():
    specs = specs_of()
    plan = plan_for(specs)
    plan["steps"][0]["model"] = {"provider": "bedrock", "model": "claude-sonnet-x", "tier": "frontier", "rationale": "..."}
    g = build_graph(stage=STAGE, plan=plan, specialists=specs, custom=[], custom_skills=[], models={})            # no role routes: the plan's own choice is used
    assert by_id(g)["lead"]["model"] == "claude-sonnet-x"
    g = build_graph(stage=STAGE, plan=plan, specialists=specs, custom=[], custom_skills=[], models={"generate": "bedrock/claude-route"})
    assert by_id(g)["lead"]["model"] == "bedrock/claude-route"


# ----------------------------------------------------------------------------- retries
def test_a_step_that_is_run_again_shows_as_retrying_until_it_finishes_and_counts_its_retries():
    g = graph()
    field, owner, art = run_events(g)
    first = [{"type": "session"}, {"type": "part", "part": field, "status": "running"}, {"type": "part", "part": field, "status": "done"}]
    n = by_id(fold_events(g, first))[owner]
    assert n["status"] == "done" and not n.get("retries")
    # validation found a problem: the validator and the agent that wrote the flagged field are run again
    rework = first + [{"type": "step", "id": "validate", "status": "retrying", "detail": "1 issue(s) found: reworking"},
                      {"type": "step", "field": field, "status": "retrying", "detail": "1 issue(s) found: reworking"}]
    mid = by_id(fold_events(g, rework))
    assert mid[owner]["status"] == "retrying" and mid[owner]["retries"] == 1 and "reworking" in mid[owner]["detail"]
    assert mid["validate"]["status"] == "retrying"
    running_again = by_id(fold_events(g, rework + [{"type": "part", "part": field, "status": "running"}]))
    assert running_again[owner]["status"] == "retrying"                         # a running event of the retry does not hide the retry
    finished = by_id(fold_events(g, rework + [{"type": "part", "part": field, "status": "running"}, {"type": "part", "part": field, "status": "done"},
                                              {"type": "node", "node": "validator", "label": "Validator: output validated ✓"}]))
    assert finished[owner]["status"] == "done" and finished[owner]["retries"] == 1 and finished["validate"]["status"] == "done"
    twice = by_id(fold_events(g, rework + [{"type": "part", "part": field, "status": "done"}, {"type": "step", "field": field, "status": "retrying"}]))
    assert twice[owner]["retries"] == 2                                          # a second rework is a second retry
    same = by_id(fold_events(g, rework + [{"type": "step", "field": field, "status": "retrying", "detail": "again"}]))
    assert same[owner]["retries"] == 1                                           # events of one attempt do not add up


def test_a_step_still_retrying_when_the_run_ends_is_closed_and_a_failed_retry_is_failed():
    g = graph()
    field, owner, _ = run_events(g)
    ev = [{"type": "step", "field": field, "status": "retrying"}]
    assert by_id(fold_events(g, ev + [{"type": "done"}]))[owner]["status"] == "done"
    assert by_id(fold_events(g, ev + [{"type": "part", "part": field, "status": "failed", "error": "still bad"}]))[owner]["status"] == "failed"
    assert by_id(fold_events(g, [{"type": "node", "node": "validator", "label": "Validator: 2 issue(s) remain after rework — flagged for gate review"}]))["validate"]["status"] == "failed"


async def test_a_json_generation_that_starts_another_attempt_tells_a_listener():
    import httpx
    from pydantic import BaseModel

    from app.integrations.llm import LlmClient, on_retry
    from app.services import usage_context as uc

    class Shape(BaseModel):
        name: str

    answers = iter(["not json", '{"name": "ok"}'])

    def handler(_r: httpx.Request) -> httpx.Response:
        content = next(answers)
        return httpx.Response(200, json={"provider": "bedrock", "model": "m", "content": content, "usage": {"promptTokens": 1, "completionTokens": 1}, "attempts": [],
                                         "tier": "auto", "truncated": False, "usageLog": []})

    c = LlmClient("http://ai-client:8081")
    c._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    uc.reset(user_id="u", project_id="p")
    seen: list[dict] = []
    with on_retry(seen.append):
        inst, _ = await c.generate_json(intent="generation", messages=[{"role": "user", "content": "x"}], schema=Shape, activity="stage.generate", max_attempts=3)
    assert inst.name == "ok" and len(seen) == 1 and seen[0]["attempt"] == 2 and seen[0]["of"] == 3 and seen[0]["reason"]

    def broken(_info: dict) -> None:
        raise RuntimeError("listener bug")

    answers = iter(["still not json", '{"name": "fine"}'])
    with on_retry(broken):                                                       # a listener that fails never fails the generation
        inst, _ = await c.generate_json(intent="generation", messages=[{"role": "user", "content": "x"}], schema=Shape, activity="stage.generate", max_attempts=3)
    assert inst.name == "fine"
    answers = iter(["bad", '{"name": "q"}'])
    inst, _ = await c.generate_json(intent="generation", messages=[{"role": "user", "content": "x"}], schema=Shape, activity="stage.generate", max_attempts=3)   # no listener: nothing breaks
    assert inst.name == "q"
