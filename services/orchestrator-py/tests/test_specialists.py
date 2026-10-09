"""Specialist agents: own instructions, own (smaller) context, own model role, and a record of what each was given."""
from types import SimpleNamespace

import pytest

from app.agents import specialists as sp
from app.agents.phase_agents import _generate_with_specialists, _minimal_value, _min_len
from app.agents.schemas import PHASE_SCHEMAS
from app.domain.models import AgentState, ContextArtifact


def _state(template=2, **kw) -> AgentState:
    ctx = [
        ContextArtifact(phase=1, type="PRD", title="Product Requirements Document", summary="prd", exact=True, content="PRD-BODY needs a queue"),
        ContextArtifact(phase=1, type="EPIC", title="E1", summary="epic", exact=True, content="EPIC-BODY"),
        ContextArtifact(phase=1, type="UNRELATED_NOTE", title="Lunch", summary="x", exact=True, content="SECRET-UNRELATED"),
    ]
    return AgentState(project_id="p", session_id="s", current_phase=template, stage_template=template, user_input="Design it.\n\n## Production scope\n- x",
                      context_window=ctx, tech_stack="Python 3.12", **kw)


def test_every_output_field_has_an_owner_except_the_code_files():
    for template, schema in PHASE_SCHEMAS.items():
        owned = [f for s in sp.BY_TEMPLATE.get(template, []) for f in s.fields]
        assert len(owned) == len(set(owned)), f"a field is owned twice in stage {template}"
        assert set(owned) <= set(schema.model_fields)
        assert {f for f in schema.model_fields if f not in owned} <= ({"files"} if template == 6 else set())


def test_registry_is_well_formed():
    assert len({s.id for s in sp.REGISTRY}) == len(sp.REGISTRY)
    assert {s.role for s in sp.REGISTRY} <= {"reason", "generate", "light"}
    for template, schema in PHASE_SCHEMAS.items():
        specs = sp.BY_TEMPLATE.get(template, [])
        assert all(a in schema.model_fields for s in specs for a in s.after)
        placed = [s.id for w in sp.waves(specs) for s in w]
        assert sorted(placed) == sorted(s.id for s in specs)


def test_waves_put_a_reader_after_its_source():
    waves = [[s.id for s in w] for w in sp.waves(sp.BY_TEMPLATE[2])]
    assert waves[0] == ["architecture-analysis"] and "hld" in waves[1] and "adr" in waves[1]


def test_artefact_type_finds_its_specialist():
    assert sp.for_type(2, "ARCH_DIAGRAM").id == "cloud-topology"
    assert sp.for_type(3, "openapi").id == "api-contract"
    assert sp.for_type(1, "user story").id == "backlog" if sp.for_type(1, "user story") else True


def test_a_specialist_sees_only_what_it_declared():
    st = _state(2)
    hld = sp._BY_ID["hld"]
    p = sp.build_prompt(hld, st, canon_block="", memory_all="", memory_redacted="", formwork_block="",
                        siblings={"components": ["COMP-A"], "mermaidArchitecture": "NOT-FOR-HLD"}, amend=None)
    assert "PRD-BODY" in p.user and "EPIC-BODY" in p.user
    assert "SECRET-UNRELATED" not in p.user                       # an artefact it did not ask for
    assert "COMP-A" in p.user and "NOT-FOR-HLD" not in p.user       # only the sibling outputs it builds on
    assert "## Production scope" not in p.user                    # the scope block is not for the agent's brief
    assert {i["layer"] for i in p.items} >= {"instructions", "input", "upstream", "sibling"}


def test_personal_memory_is_in_the_prompt_but_not_in_the_record():
    st = _state(2)
    spec = sp._BY_ID["hld"]
    p = sp.build_prompt(spec, st, canon_block="CANON", memory_all="MEM-TEAM MEM-PERSONAL", memory_redacted="MEM-TEAM (+1 personal)",
                        formwork_block="", siblings={}, amend=None)
    assert "MEM-PERSONAL" in p.system and "MEM-PERSONAL" not in p.recorded_system
    assert "MEM-TEAM" in p.recorded_system and "CANON" in p.recorded_system


@pytest.mark.asyncio
async def test_engine_runs_each_agent_with_its_own_prompt_role_and_leaves_a_record():
    schema = PHASE_SCHEMAS[4]
    seen: list[dict] = []

    class Llm:
        async def generate_json(self, *, intent, tag, messages, schema, max_tokens, model=None, role=None, max_attempts=3):
            fields = list(schema.model_fields)
            seen.append({"tag": tag, "role": role, "system": messages[0]["content"], "user": messages[1]["content"], "fields": fields})
            vals = {f: _minimal_value(schema.model_fields[f].annotation, "x", _min_len(schema.model_fields[f])) for f in fields}
            return schema.model_validate(vals), SimpleNamespace(provider="mock", model="mock-1", content="{}", tier="auto", attempts=[],
                                                                 usage={"promptTokens": 10, "completionTokens": 5})

    parts: dict[str, dict] = {}

    class Db:
        async def list_generation_parts(self, *a):
            return []

        async def clear_generation_parts(self, *a):
            parts.clear()

        async def upsert_generation_part(self, **k):
            parts[k["field"]] = k

    deps = SimpleNamespace(llm=Llm(), db=Db(), settings=SimpleNamespace(PER_ARTIFACT_MAX_PARALLEL=4), formworks=None)
    st = _state(4)
    events = []
    data, res = await _generate_with_specialists(
        deps=deps, state=st, schema=schema, base_tag="t", intent="generation", max_tokens=1000, model=None, emit=events.append,
        canon_block="", memory_all="", memory_redacted="", stage_system="STAGE", stage_user="STAGE-USER", stage_system_recorded="STAGE", amend=None)
    assert {c["tag"].split(":")[-1] for c in seen} == {s.id for s in sp.BY_TEMPLATE[4]}
    by = {c["tag"].split(":")[-1]: c for c in seen}
    assert by["test-planning"]["role"] == "reason" and by["traceability"]["role"] == "light"
    assert all("SECRET-UNRELATED" not in c["user"] and "SECRET-UNRELATED" not in c["system"] for c in seen)
    assert "Prompt" not in by["api-tests"]["system"]
    # the agent that builds the RTM is shown the test cases written before it, and nothing it did not ask for
    assert "xrayTests" in by["traceability"]["user"] and "testStrategyMarkdown" not in by["traceability"]["user"]
    order = [c["tag"].split(":")[-1] for c in seen]
    assert order.index("test-cases") < order.index("traceability") and order.index("test-planning") < order.index("test-cases")
    # every field has a record saying who wrote it, on what, with which context
    assert set(st.agent_runs) == set(schema.model_fields)
    run = st.agent_runs["rtmMarkdown"]
    assert run["agentId"] == "traceability" and run["model"] == "mock-1" and run["context"] and run["user"] and run["system"]
    assert parts["rtmMarkdown"]["run"]["agentId"] == "traceability"
    assert res.usage["promptTokens"] == 10 * len(seen)


@pytest.mark.asyncio
async def test_regenerating_one_artefact_runs_only_its_agent_and_reuses_the_rest():
    schema = PHASE_SCHEMAS[4]
    calls: list[str] = []

    class Llm:
        async def generate_json(self, *, intent, tag, messages, schema, max_tokens, model=None, role=None, max_attempts=3):
            calls.append(tag.split(":")[-1])
            fields = list(schema.model_fields)
            vals = {f: _minimal_value(schema.model_fields[f].annotation, "new", _min_len(schema.model_fields[f])) for f in fields}
            return schema.model_validate(vals), SimpleNamespace(provider="mock", model="m", content="{}", tier="auto", attempts=[],
                                                                 usage={"promptTokens": 1, "completionTokens": 1})

    from pydantic import TypeAdapter
    cached = {}
    for f, fi in schema.model_fields.items():
        v = _minimal_value(fi.annotation, "old", _min_len(fi))
        cached[f] = {"field": f, "status": "done", "value_json": TypeAdapter(fi.annotation).dump_json(v).decode(),
                     "run": {"agentId": "earlier", "agentName": "Earlier", "fields": [f]}}

    class Db:
        async def list_generation_parts(self, *a):
            return list(cached.values())

        async def upsert_generation_part(self, **k):
            pass

    deps = SimpleNamespace(llm=Llm(), db=Db(), settings=SimpleNamespace(PER_ARTIFACT_MAX_PARALLEL=4), formworks=None)
    st = _state(4, retrigger_fields=["rtmMarkdown"])
    await _generate_with_specialists(
        deps=deps, state=st, schema=schema, base_tag="t", intent="generation", max_tokens=1000, model=None, emit=lambda e: None,
        canon_block="", memory_all="", memory_redacted="", stage_system="S", stage_user="U", stage_system_recorded="S", amend=None)
    assert calls == ["traceability"]
    assert st.agent_runs["rtmMarkdown"]["agentId"] == "traceability"
    assert st.agent_runs["xrayTests"]["agentId"] == "earlier" and st.agent_runs["xrayTests"]["reused"] is True


# ---------------------------------------------------------------- the definition files
def test_every_definition_file_loads_and_matches_its_folder_and_schema():
    from app.services import agent_catalog as ac
    cat = ac.catalog()
    assert len({a["id"] for a in cat}) == len(cat)
    for a in cat:
        assert a["description"] and a["body"], a["id"]
        if a["runtime"] == "specialist":
            assert a["path"].startswith(f"generators/stage-{a['stage']}-"), a["path"]
            assert "## Notes" not in a["body"] and a["notes"]                 # documentation is not sent to the model
        else:
            assert a["runtime"] in ("native", "proposed")
    assert sum(a["runtime"] == "specialist" for a in cat) == len(sp.REGISTRY) == 29
    assert {a["id"] for a in cat if a["status"] == "proposed"} == {"memory-distiller", "impact-analyst", "review-summariser"}


def test_native_agents_point_at_real_prompts_and_real_code():
    import pathlib

    from app.services import agent_catalog as ac
    root = pathlib.Path(__file__).resolve().parents[1]
    for a in ac.catalog():
        if a["runtime"] != "native":
            continue
        path = a["entrypoint"].split("::")[0]
        assert (root / path).exists(), f"{a['id']}: entrypoint {path} does not exist"
        assert a["role"] in ac.ROLES


def test_the_loader_rejects_a_bad_definition(tmp_path):
    from app.services import agent_catalog as ac
    good = "---\nid: x\nname: X\nversion: 1\ncategory: generator\nruntime: native\nstatus: active\ndescription: d\nrole: light\n---\nbody"
    (tmp_path / "x.md").write_text(good)
    assert ac.load_agents(tmp_path)[0]["id"] == "x"
    for name, text, why in [
        ("y.md", good, "must equal the file name"),
        ("x.md", good.replace("role: light", "role: huge"), "role must be one of"),
        ("x.md", good.replace("runtime: native", "runtime: specialist"), "specialist agents need"),
        ("x.md", good.replace("role: light", "role: light\nprompts: [no.such.prompt]"), "not in the prompt library"),
    ]:
        d = tmp_path / name.replace(".md", "") ; d.mkdir(exist_ok=True)
        (d / name).write_text(text)
        with pytest.raises(ac.AgentPackError, match=why):
            ac.load_agents(d)


def test_editing_an_agent_file_changes_what_the_model_is_sent(tmp_path, monkeypatch):
    """The file body IS the agent's instruction: no code change is needed to tune it."""
    import shutil

    from app.services import agent_catalog as ac
    shutil.copytree(ac.default_agents_dir(), tmp_path / "agents")
    f = tmp_path / "agents/generators/stage-1-requirements/prd.md"
    f.write_text(f.read_text().replace("# Role", "# Role\nTUNED-BY-AN-EDITOR", 1))
    spec = ac.load_agents(tmp_path / "agents")
    prd = next(a for a in spec if a["id"] == "prd")
    assert "TUNED-BY-AN-EDITOR" in sp._from_definition(prd).instructions
