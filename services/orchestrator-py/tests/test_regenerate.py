"""Selective regeneration of a stage's artifacts, including after approval."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agents.schemas import PHASE_SCHEMAS
from app.domain.errors import SdlcError
from app.domain.models import AgentState
from app.services.flow import FlowService

from .conftest import FakeAudit, make_user

FIELDS = list(PHASE_SCHEMAS[2].model_fields)


class Wf:
    stage = {"template": 2, "reviewerRole": "SA", "name": "Solution Architecture",
             "team": ["SA"], "seq": 2, "key": "sa"}

    async def stage_by_seq(self, *_): return self.stage
    async def view(self, *_):
        return {"stages": [self.stage, {"seq": 3, "key": "ta", "dependsOn": ["sa"]}]}


class Dynamo:
    def __init__(self, status: str) -> None:
        self.state = {"status": status}
        self.stale: list[int] = []
        self.put: list[str] = []

    async def get_phase_state(self, *_): return self.state
    async def list_phase_states(self, *_): return [{"SK": "PHASE#3", "status": "APPROVED"}]
    async def put_phase_state(self, *, status, **_): self.put.append(status)
    async def mark_phase_stale(self, *, phase, **_): self.stale.append(phase)


class Db:
    def __init__(self, parts: list[str]) -> None:
        self.parts, self.phase_calls = parts, []

    async def list_generation_parts(self, *_):
        return [{"field": f, "status": "done", "value_json": '"x"'} for f in self.parts]

    async def set_project_phase(self, *a): self.phase_calls.append(a)


def make(status="APPROVED", parts=FIELDS):
    dyn, db, audit = Dynamo(status), Db(parts), FakeAudit()
    return FlowService(db, dyn, audit, None, None, Wf(), None), dyn, db, audit


async def test_regenerates_only_selected_after_approval_and_flags_downstream() -> None:
    flow, dyn, db, audit = make()
    out = await flow.regenerate_parts("p", 2, [FIELDS[0]], make_user("SUPER_ADMIN"))
    assert out["fields"] == [FIELDS[0]] and out["previousStatus"] == "APPROVED"
    assert dyn.put == ["IN_PROGRESS"] and dyn.stale == [3] and db.phase_calls
    assert "stage.parts_regenerated" in audit.events


async def test_empty_selection_means_all_and_needs_no_cache() -> None:
    flow, *_ = make(parts=[])
    out = await flow.regenerate_parts("p", 2, [], make_user("SUPER_ADMIN"))
    assert out["fields"] == FIELDS


async def test_partial_selection_needs_saved_output_for_the_rest() -> None:
    flow, *_ = make(parts=[])
    with pytest.raises(SdlcError, match="missing"):
        await flow.regenerate_parts("p", 2, [FIELDS[0]], make_user("SUPER_ADMIN"))


@pytest.mark.parametrize(("status", "fields", "msg"), [
    ("NOT_STARTED", [], "not run"),
    ("IN_PROGRESS", [], "already generating"),
    ("APPROVED", ["nope"], "Unknown"),
])
async def test_rejects_invalid_requests(status, fields, msg) -> None:
    flow, dyn, *_ = make(status)
    with pytest.raises(SdlcError, match=msg):
        await flow.regenerate_parts("p", 2, fields, make_user("SUPER_ADMIN"))
    assert dyn.put == []  # nothing rewound on a rejected request


async def test_requires_write_permission() -> None:
    flow, dyn, *_ = make()
    flow._authz = SimpleNamespace(get_membership_role=lambda *_: _none())
    with pytest.raises(SdlcError):
        await flow.regenerate_parts("p", 2, [], make_user("QA"))
    assert dyn.put == []


async def _none():
    return None


# ---- reviewer's confirmed production scope must drive generation itself ----------
SCOPE = (
    "Regenerate the design.\n\n## Production scope (confirmed by the reviewer)\n"
    "- Produce ONLY these artifacts: LLD, COMPONENT_DIAGRAM.\n"
    "- Do NOT produce: OPENAPI, DBML, CDK, PLANTUML, LLD_DIAGRAM.\n"
)
OUT_OF_SCOPE = {"plantumlDiagrams", "mermaidSequence", "openapiYaml", "dbmlSchema", "cdkStack"}


def test_exact_type_names_beat_the_coarse_concept_match() -> None:
    from app.agents.phase_agents import labels_excluded, production_scope

    scope = production_scope(SCOPE)
    for t in ("OPENAPI", "DBML", "CDK", "PLANTUML", "LLD_DIAGRAM"):
        assert labels_excluded(scope, t), t          # previously diagrams leaked through
    for t in ("LLD", "COMPONENT_DIAGRAM"):
        assert not labels_excluded(scope, t), t


def test_scope_skips_only_excluded_fields_of_the_stage() -> None:
    from app.agents.phase_agents import scope_skipped_fields

    st = AgentState(project_id="p", session_id="s", user_input=SCOPE, current_phase=3, stage_template=3)
    fields = list(PHASE_SCHEMAS[3].model_fields)
    assert set(scope_skipped_fields(st, fields)) == OUT_OF_SCOPE
    assert scope_skipped_fields(st.model_copy(update={"user_input": "no scope"}), fields) == []


async def test_split_never_calls_the_model_for_out_of_scope_fields() -> None:
    from app.agents.phase_agents import _generate_phase_split

    from .test_resume import FakeLlm, PartDb

    schema = PHASE_SCHEMAS[3]
    calls: list[str] = []

    class Llm(FakeLlm):
        async def generate_json(self, *, tag, schema, **kw):
            calls.append(tag.rsplit(":", 1)[1])
            fn = tag.rsplit(":", 1)[1]
            fi = schema.model_fields[fn]
            from app.agents.phase_agents import _min_len, _minimal_value
            res = SimpleNamespace(provider="p", model="m", attempts=[], tier="x", content="",
                                  usage={"promptTokens": 1, "completionTokens": 1})
            val = _minimal_value(fi.annotation, "ok", _min_len(fi)) if fi.is_required() else fi.get_default(
                call_default_factory=True)
            return schema(**{fn: val}), res

    events: list[dict] = []
    db = PartDb()
    deps = SimpleNamespace(llm=Llm(), db=db, settings=SimpleNamespace(PER_ARTIFACT_MAX_PARALLEL=3))
    state = AgentState(project_id="p", session_id="s", user_input=SCOPE, current_phase=3, stage_template=3)
    data, _ = await _generate_phase_split(
        deps=deps, state=state, system="s", user="u", schema=schema, base_tag="t", intent="x",
        max_tokens=10, model=None, emit=events.append)
    assert set(calls) == set(schema.model_fields) - OUT_OF_SCOPE
    assert sum("skipped" in e.get("label", "") for e in events) == len(OUT_OF_SCOPE)
    assert "outside the confirmed production scope" in data.cdkStack
