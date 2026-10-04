from app.agile.engine import (
    InstanceRef, IterationRef, ReleaseRef, allocate_seqs, expand, instance_key, iteration_block,
)
from app.agile.templates import kanban_workflow, scrum_workflow
from app.services.workflow import derive, validate_workflow


def _base():
    return derive(scrum_workflow())


def _materialise(base, iters, rels):
    """Mimic the service: allocate slots sprint by sprint / release by release."""
    inst: list[InstanceRef] = []
    for it in iters:
        first = it.number == 1
        alloc = allocate_seqs(base["stages"], inst, scope="iteration", first=first)
        inst += [InstanceRef(seq, instance_key(k, it.label), k, "iteration", it.id, None) for k, seq in alloc.items()]
    for rel in rels:
        if rel.status == "open":
            continue
        first = not any(i.scope == "release" for i in inst)
        alloc = allocate_seqs(base["stages"], inst, scope="release", first=first)
        inst += [InstanceRef(seq, instance_key(k, rel.code), k, "release", None, rel.id) for k, seq in alloc.items()]
    return inst


def test_templates_are_valid_workflows():
    assert validate_workflow(scrum_workflow()) == []
    assert validate_workflow(kanban_workflow()) == []


def test_block_has_single_entry_and_sink():
    b = iteration_block(_base()["stages"])
    assert (b.entry, b.sink) == ("refine", "retro")
    assert b.keys == ("refine", "plan", "build", "review", "retro")


def test_before_any_sprint_only_project_stages_are_visible():
    v = expand(_base(), iterations=[], releases=[], instances=[])
    assert [s["key"] for s in v["stages"]] == ["vision", "runway"]
    assert v["levels"] == [[1], [2]]


def test_sprint_one_reuses_base_slots_and_chains_after_the_runway():
    base = _base()
    it1 = IterationRef("i1", 1, "S-001", "r1", "active")
    v = expand(base, iterations=[it1], releases=[ReleaseRef("r1", 1, "R-001", "open")],
               instances=_materialise(base, [it1], []))
    by = {s["key"]: s for s in v["stages"]}
    assert [by[f"{k}@S-001"]["seq"] for k in ("refine", "plan", "build", "review", "retro")] == [3, 4, 5, 6, 7]
    assert by["refine@S-001"]["dependsOn"] == ["runway"]
    assert by["plan@S-001"]["dependsOn"] == ["refine@S-001"]
    assert by["build@S-001"]["name"] == "Build & Test · S-001" and by["build@S-001"]["iteration"] == 1
    assert by["build@S-001"]["agileRole"] == "build" and by["build@S-001"]["gateMode"] == "auto"
    assert v["levels"] == [[1], [2], [3], [4], [5], [6], [7]]


def test_sprints_are_strictly_sequential_and_get_fresh_slots():
    base = _base()
    its = [IterationRef("i1", 1, "S-001", "r1", "closed"), IterationRef("i2", 2, "S-002", "r1", "active"),
           IterationRef("i3", 3, "S-003", "r1", "planned")]
    v = expand(base, iterations=its, releases=[ReleaseRef("r1", 1, "R-001", "open")],
               instances=_materialise(base, its, []))
    by = {s["key"]: s for s in v["stages"]}
    assert by["refine@S-002"]["dependsOn"] == ["runway", "retro@S-001"]
    assert by["refine@S-003"]["dependsOn"] == ["runway", "retro@S-002"]
    seqs = [by[f"{k}@S-002"]["seq"] for k in ("refine", "plan", "build", "review", "retro")]
    assert seqs == [9, 10, 11, 12, 13]                      # above the base slots (incl. the reserved release slot 8)
    assert by["refine@S-003"]["seq"] == 14
    assert len({s["seq"] for s in v["stages"]}) == len(v["stages"])  # every slot unique
    lvl = {s["key"]: s["level"] for s in v["stages"]}
    assert lvl["refine@S-002"] > lvl["retro@S-001"] and lvl["refine@S-003"] > lvl["retro@S-002"]


def test_cancelled_sprint_is_skipped_in_the_chain():
    base = _base()
    its = [IterationRef("i1", 1, "S-001", "r1", "closed"), IterationRef("i2", 2, "S-002", "r1", "cancelled"),
           IterationRef("i3", 3, "S-003", "r1", "active")]
    v = expand(base, iterations=its, releases=[ReleaseRef("r1", 1, "R-001", "open")],
               instances=_materialise(base, its, []))
    keys = {s["key"] for s in v["stages"]}
    assert not any(k.endswith("@S-002") for k in keys)
    assert next(s for s in v["stages"] if s["key"] == "refine@S-003")["dependsOn"] == ["runway", "retro@S-001"]


def test_release_stage_waits_for_the_last_sprint_of_its_release():
    base = _base()
    its = [IterationRef("i1", 1, "S-001", "r1", "closed"), IterationRef("i2", 2, "S-002", "r1", "closed"),
           IterationRef("i3", 3, "S-003", "r2", "active")]
    rels = [ReleaseRef("r1", 1, "R-001", "hardening"), ReleaseRef("r2", 2, "R-002", "open")]
    v = expand(base, iterations=its, releases=rels, instances=_materialise(base, its, rels))
    by = {s["key"]: s for s in v["stages"]}
    rel = by["release@R-001"]
    assert rel["seq"] == 8                                   # first release reuses the base slot
    assert "retro@S-002" in rel["dependsOn"] and "retro@S-001" not in rel["dependsOn"]
    assert rel["release"] == "R-001" and rel["agileRole"] == "release"
    assert "release@R-002" not in by                         # not opened yet → not materialised


def test_second_release_gets_a_fresh_slot():
    base = _base()
    its = [IterationRef("i1", 1, "S-001", "r1", "closed"), IterationRef("i2", 2, "S-002", "r2", "closed")]
    rels = [ReleaseRef("r1", 1, "R-001", "closed"), ReleaseRef("r2", 2, "R-002", "hardening")]
    v = expand(base, iterations=its, releases=rels, instances=_materialise(base, its, rels))
    by = {s["key"]: s for s in v["stages"]}
    assert by["release@R-001"]["seq"] == 8 and by["release@R-002"]["seq"] > 13
    assert "retro@S-002" in by["release@R-002"]["dependsOn"]


def test_expansion_is_deterministic():
    base = _base()
    its = [IterationRef(f"i{n}", n, f"S-{n:03d}", "r1", "closed") for n in range(1, 7)]
    inst = _materialise(base, its, [])
    a = expand(base, iterations=its, releases=[ReleaseRef("r1", 1, "R-001", "open")], instances=inst)
    b = expand(base, iterations=list(reversed(its)), releases=[ReleaseRef("r1", 1, "R-001", "open")], instances=list(reversed(inst)))
    assert a == b


def test_waterfall_workflow_is_unchanged_by_expansion():
    from app.services.workflow import default_workflow
    base = derive(default_workflow())
    v = expand(base, iterations=[], releases=[], instances=[])
    assert [s["seq"] for s in v["stages"]] == [s["seq"] for s in base["stages"]]
    assert v["levels"] == base["levels"]


def test_iterative_workflow_needs_an_iteration_stage():
    w = scrum_workflow()
    for st in w.stages:
        st.scope, st.agileRole = "project", None
    assert any("at least one iteration-scoped stage" in e for e in validate_workflow(w))


def test_an_agile_role_can_be_used_only_once():
    w = scrum_workflow()
    w.stages[3].agileRole = "refine"
    assert any("only one stage" in e for e in validate_workflow(w))


def test_iteration_block_must_have_a_single_entry():
    w = scrum_workflow()
    w.stages[2].dependsOn = ["retro"]          # refine now waits for retro: no entry stage left
    assert any("exactly one entry stage" in e for e in validate_workflow(w))


def test_project_stage_cannot_depend_on_a_sprint_stage():
    w = scrum_workflow()
    w.stages[1].dependsOn = ["vision", "retro"]  # runway depends on the sprint's closing stage
    assert any("cannot depend on iteration-scoped" in e for e in validate_workflow(w))


def test_waterfall_cannot_carry_agile_fields():
    from app.services.workflow import default_workflow
    w = default_workflow()
    w.stages[0].scope = "iteration"
    assert any("needs a Scrum or Kanban" in e for e in validate_workflow(w))
