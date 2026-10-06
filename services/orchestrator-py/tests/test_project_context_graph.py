"""Project-level context graph: shared context, stages, attachments, artifacts and who builds on them."""
from __future__ import annotations

from app.services.project_context_graph import build_project_graph

STAGES = [
    {"key": "po", "seq": 1, "name": "Requirements", "template": 1, "dependsOn": [], "outputs": ["PRD"]},
    {"key": "sa", "seq": 2, "name": "Solution", "template": 2, "dependsOn": ["po"], "outputs": ["HLD"]},
    {"key": "ta", "seq": 3, "name": "Technical Design", "template": 3, "dependsOn": ["sa"], "outputs": ["LLD"]},
    {"key": "qa", "seq": 4, "name": "Test Strategy", "template": 5, "dependsOn": ["po"], "outputs": ["TEST_STRATEGY"]},
]
ARTS = [{"id": "a1", "phase": 1, "type": "PRD", "title": "PRD", "version": 2}, {"id": "a2", "phase": 2, "type": "HLD", "title": "HLD", "version": 1}]


def _g(**kw):
    base = dict(stages=STAGES, levels=[[1], [2, 4], [3]], states={1: "APPROVED", 2: "PENDING_REVIEW"}, artifacts=ARTS,
                attachments={1: [{"id": "f1", "filename": "reqs.pdf"}]}, project={"name": "Pay", "tech_stack": "Python", "tech_stack_source": "ta"},
                manifests={2: {"totals": {"tokens": 5000, "items": 9}}}, canon_stages={1, 2}, template_names=["House PRD"], codebase_files=0)
    base.update(kw)
    return build_project_graph(**base)


def _edges(g, label):
    return {(e["from"], e["to"]) for e in g["edges"] if e["label"] == label}


def test_every_stage_is_a_node_with_its_state_size_and_level():
    g = _g()
    st = {n["id"]: n for n in g["nodes"] if n["kind"] == "stage"}
    assert set(st) == {"stage:1", "stage:2", "stage:3", "stage:4"}
    assert st["stage:2"]["status"] == "PENDING_REVIEW" and st["stage:2"]["tokens"] == 5000 and st["stage:2"]["source"]["ran"]
    assert st["stage:3"]["status"] == "NOT_STARTED" and not st["stage:3"]["source"]["ran"]
    assert (st["stage:1"]["level"], st["stage:2"]["level"], st["stage:4"]["level"], st["stage:3"]["level"]) == (0, 1, 1, 2)
    assert _edges(g, "feeds") == {("stage:1", "stage:2"), ("stage:2", "stage:3"), ("stage:1", "stage:4")}


def test_artifacts_hang_off_their_producer_and_feed_only_downstream_stages():
    g = _g()
    assert _edges(g, "produced") == {("stage:1", "art:a1"), ("stage:2", "art:a2")}
    uses = _edges(g, "builds on")
    assert {("art:a1", "stage:2"), ("art:a1", "stage:3"), ("art:a1", "stage:4"), ("art:a2", "stage:3")} == uses
    assert ("art:a1", "stage:1") not in uses and ("art:a2", "stage:4") not in uses     # never its own stage; not a sibling branch


def test_uploaded_files_attach_to_the_stage_they_were_uploaded_to():
    g = _g()
    assert _edges(g, "attached") == {("att:f1", "stage:1")}
    att = next(n for n in g["nodes"] if n["id"] == "att:f1")
    assert att["kind"] == "attachment" and att["source"]["stage"] == "Requirements"
    assert g["totals"]["attachments"] == 1


def test_shared_project_context_applies_to_the_stages_it_actually_reaches():
    g = _g()
    kinds = {n["id"] for n in g["nodes"] if n["level"] == -1}
    assert kinds == {"project", "stack", "canon", "templates"}                            # no codebase attached
    assert {t for f, t in _edges(g, "applies to") if f == "canon"} == {"stage:1", "stage:2"}
    assert len({t for f, t in _edges(g, "applies to") if f == "stack"}) == 4
    g2 = _g(codebase_files=40, canon_stages=set(), template_names=[], project={"name": "Pay"})
    assert {n["id"] for n in g2["nodes"] if n["level"] == -1} == {"project", "stack", "codebase"}
    assert next(n for n in g2["nodes"] if n["id"] == "stack")["status"] == "excluded"     # undecided
    assert {t for f, t in _edges(g2, "applies to") if f == "codebase"} == {"stage:3", "stage:4"}   # build stages only


def test_a_huge_project_is_capped_and_says_so():
    many = [{"id": f"x{i}", "phase": 1, "type": "PRD", "title": f"t{i}", "version": 1} for i in range(300)]
    g = _g(artifacts=many)
    assert g["totals"]["artifacts"] == 150 and g["totals"]["truncated"] is True
    assert sum(1 for n in g["nodes"] if n["kind"] == "artifact") == 150
