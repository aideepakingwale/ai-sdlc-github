"""Validation agent + content validators + diagram synth fallback."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.agents import phase_agents as pa
from app.agents.schemas import ValidationIssue, ValidationVerdict
from app.domain.models import AgentState
from app.services import content_validators as cv
from app.services.chat import ChatService


# ---------------------------------------------------------------- content validators
def test_validate_mermaid_flags_missing_header_and_unbalanced():
    assert cv.validate_mermaid("A --> B")  # no diagram-type header
    assert any("bracket" in m for m in cv.validate_mermaid("flowchart LR\n A[Start --> B[End]"))
    assert cv.validate_mermaid("flowchart LR\n A[Start] --> B[End]") == []


def test_validate_mermaid_catches_semicolon_in_sequence_message():
    bad = "sequenceDiagram\n  U->>API: POST /items; then commit"
    issues = cv.validate_mermaid(bad)
    assert any(";" in m for m in issues)
    good = "sequenceDiagram\n  U->>API: POST /items, then commit"
    assert cv.validate_mermaid(good) == []


def test_validate_yaml_and_json_and_plantuml():
    assert cv.validate_yaml("a: [1, 2")  # unterminated flow seq
    assert cv.validate_yaml("name: ci\nsteps:\n  - build\n") == []
    assert cv.validate_json_str('{"a": 1,}')  # trailing comma
    assert cv.validate_json_str('{"a": 1}') == []
    assert any("enduml" in m for m in cv.validate_plantuml("@startuml\nA -> B"))
    assert cv.validate_plantuml("@startuml\nA -> B\n@enduml") == []


def test_syntactic_issues_walks_fields_by_name():
    data = {
        "mermaidArchitecture": "A --> B",  # no header -> flagged
        "workflowYaml": "name: ci\nok: true\n",  # valid
        "plantumlDiagrams": ["@startuml\nA -> B"],  # missing @enduml -> flagged
        "prose": "just text, ignored",
    }
    issues = cv.syntactic_issues(data)
    fields = {f for f, _ in issues}
    assert "mermaidArchitecture (mermaid diagram)" in fields
    assert any("plantumlDiagrams" in f for f in fields)
    assert not any("prose" in f for f in fields)


# ---------------------------------------------------------------- deterministic diagram repair
def test_autofix_mermaid_strips_fence_and_preamble():
    raw = "Here is the diagram:\n```mermaid\nflowchart LR\n A --> B\n```"
    fixed = cv.autofix_mermaid(raw)
    assert fixed.startswith("flowchart LR")
    assert "```" not in fixed and "Here is" not in fixed
    assert cv.validate_mermaid(fixed) == []


def test_autofix_mermaid_fixes_sequence_semicolon():
    bad = "sequenceDiagram\n  U->>API: POST /items; then commit"
    fixed = cv.autofix_mermaid(bad)
    assert ";" not in fixed
    assert cv.validate_mermaid(fixed) == []


def test_autofix_plantuml_wraps_directives():
    fixed = cv.autofix_plantuml("A -> B")
    assert fixed.startswith("@startuml") and fixed.rstrip().endswith("@enduml")
    assert cv.validate_plantuml(fixed) == []


def test_repair_diagram_returns_remaining_when_unfixable():
    # missing header can't be safely synthesised deterministically → still flagged
    fixed, remaining = cv.repair_diagram("A --> B", "mermaid")
    assert remaining  # deterministic fix cannot invent a diagram type header
    clean, none_left = cv.repair_diagram("flowchart LR\n A[x] --> B[y]", "mermaid")
    assert none_left == []


# ---------------------------------------------------------------- diagram synth fallback
class _Comp(BaseModel):
    name: str
    technology: str = ""
    dependsOn: list[str] = []
    collaborators: list[str] = []


def test_synth_architecture_builds_nodes_and_edges_from_components():
    comps = [
        _Comp(name="API Service", technology="ECS Fargate", dependsOn=["Aurora DB", "Cache"]),
        _Comp(name="Aurora DB", technology="Aurora PostgreSQL"),
        _Comp(name="Cache", technology="Redis ElastiCache"),
    ]
    spec = pa._synth_architecture(comps, title="Deployment", direction="TB", deps_attr="dependsOn")
    assert spec is not None
    assert len(spec.nodes) == 3
    assert {e.toId for e in spec.edges}  # edges resolved to real node ids
    # technology hints select real service icons, not the generic box
    services = {n.label: n.service for n in spec.nodes}
    assert services["API Service"] == "fargate"
    assert services["Aurora DB"] == "aurora"
    assert services["Cache"] == "cache"


def test_synth_architecture_none_when_empty():
    assert pa._synth_architecture([], title="x", direction="TB", deps_attr="dependsOn") is None


# ---------------------------------------------------------------- validation agent
class _Out(BaseModel):
    mermaidArchitecture: str = "flowchart LR\n A[Start] --> B[End]"
    hldNarrative: str = "# HLD\nSolid design."


class _FakeLlm:
    def __init__(self, verdict: ValidationVerdict):
        self.verdict = verdict
        self.calls = 0

    async def generate_json(self, **kwargs):
        self.calls += 1
        return self.verdict, SimpleNamespace(provider="mock", model="m", usage={}, attempts=1, content="")


def _state() -> AgentState:
    return AgentState(
        project_id="p1",
        session_id="s1",
        current_phase=2,
        stage_template=2,
        stage_name="Solution Architecture",
        user_input="Design the payment service",
    )


def _deps(llm, settings) -> pa.AgentDeps:
    from .conftest import FakeAudit

    return pa.AgentDeps(
        llm=llm,
        mcp=None,
        db=None,
        audit=FakeAudit(),
        rag=None,
        content=None,
        monitor=None,
        settings=settings,
    )


async def test_validate_output_syntax_overrides_llm_pass():
    """A clean-looking LLM verdict must still fail if a deterministic syntax
    error is present — syntax checks are authoritative."""
    out = _Out(mermaidArchitecture="A --> B")  # no header: broken
    llm = _FakeLlm(ValidationVerdict(ok=True, issues=[], reworkInstructions=""))
    verdict = await pa._validate_output(_deps(llm, SimpleNamespace()), _state(), lambda e: None, out)
    assert verdict.ok is False
    assert any(i.severity == "error" for i in verdict.issues)
    assert verdict.reworkInstructions  # rework text synthesised


async def test_validate_output_passes_clean_content():
    out = _Out()
    llm = _FakeLlm(ValidationVerdict(ok=True, issues=[], reworkInstructions=""))
    verdict = await pa._validate_output(_deps(llm, SimpleNamespace()), _state(), lambda e: None, out)
    assert verdict.ok is True


async def test_generate_validated_reworks_then_passes(monkeypatch):
    settings = SimpleNamespace(VALIDATION_ENABLED=True, VALIDATION_MAX_REPAIRS=1)
    gen_calls: list[str | None] = []

    async def fake_generate(deps, state, emit, *, rework=None):
        gen_calls.append(rework)
        return _Out(hldNarrative="reworked" if rework else "first")

    verdicts = [
        ValidationVerdict(ok=False, issues=[ValidationIssue(problem="x")], reworkInstructions="fix x"),
        ValidationVerdict(ok=True),
    ]

    async def fake_validate(deps, state, emit, out):
        return verdicts[min(len(gen_calls) - 1, len(verdicts) - 1)]

    monkeypatch.setattr(pa, "_generate", fake_generate)
    monkeypatch.setattr(pa, "_validate_output", fake_validate)
    out = await pa._generate_validated(_deps(None, settings), _state(), lambda e: None)
    assert gen_calls == [None, "fix x"]  # initial run + one rework with instructions
    assert out.hldNarrative == "reworked"


async def test_generate_validated_is_bounded(monkeypatch):
    """A persistently failing verdict must stop after VALIDATION_MAX_REPAIRS."""
    settings = SimpleNamespace(VALIDATION_ENABLED=True, VALIDATION_MAX_REPAIRS=1)
    gen_calls: list[str | None] = []

    async def fake_generate(deps, state, emit, *, rework=None):
        gen_calls.append(rework)
        return _Out()

    async def fake_validate(deps, state, emit, out):
        return ValidationVerdict(
            ok=False, issues=[ValidationIssue(problem="still bad")], reworkInstructions="try again"
        )

    monkeypatch.setattr(pa, "_generate", fake_generate)
    monkeypatch.setattr(pa, "_validate_output", fake_validate)
    await pa._generate_validated(_deps(None, settings), _state(), lambda e: None)
    assert len(gen_calls) == 2  # initial + exactly one rework, then give up


async def test_build_phase_prompt_injects_user_context_block():
    """curated @references + attachments reach the phase system prompt."""
    from app.agents.prompts import build_phase_prompt

    system, _ = build_phase_prompt(
        phase=2, context_block="", rag_block="", user_input="design it",
        amend_comments=None, user_context_block="### Attachment — spec.md\nMUST support SSO.",
    )
    assert "Context the requester attached for this stage" in system
    assert "MUST support SSO." in system


async def test_resolve_extra_context_pins_references_and_attachments():
    """chat._resolve_extra_context renders curated artifacts + text attachments
    into one labelled block, honouring order and skipping cross-project rows."""
    from types import SimpleNamespace

    class _Content:
        def __init__(self, kv):
            self.kv = kv

        async def get(self, key):
            return self.kv.get(key)

    class _Db:
        async def get_artefacts_by_ids(self, ids):
            return [
                {"id": "a1", "project_id": "p1", "phase": 2, "type": "HLD", "title": "High-Level Design",
                 "content": "", "storage_key": "k-hld"},
                {"id": "aX", "project_id": "other", "phase": 2, "type": "HLD", "title": "leak",
                 "content": "SECRET", "storage_key": None},
            ]

        async def get_attachments_by_ids(self, ids):
            return [
                {"id": "att1", "project_id": "p1", "filename": "notes.txt", "is_text": True, "storage_key": "k-att"},
                {"id": "att2", "project_id": "p1", "filename": "logo.png", "is_text": False, "storage_key": "k-png"},
            ]

        async def get_formworks_by_ids(self, ids):
            return [
                {"id": "fw1", "project_id": None, "name": "PRD Template", "template": "TEMPLATE BODY"},
                {"id": "fwX", "project_id": "other", "name": "leak-tpl", "template": "TPL SECRET"},
            ]

    svc = ChatService.__new__(ChatService)
    svc._db = _Db()
    svc._deps = SimpleNamespace(content=_Content({"k-hld": "HLD BODY", "k-att": "ATTACHMENT TEXT"}))

    block = await svc._resolve_extra_context("p1", ["a1", "aX"], ["att1", "att2"], ["fw1", "fwX"], lambda e: None)
    assert "HLD BODY" in block
    assert "SECRET" not in block  # cross-project reference + template dropped
    assert "ATTACHMENT TEXT" in block
    assert "logo.png (binary; not inlined)" in block
    assert "TEMPLATE BODY" in block  # platform template (project_id NULL) resolves


async def test_delete_project_purges_every_store():
    """delete_project removes content-store files + Dynamo phase-states +
    all Postgres rows, and audits it, once authz permits."""
    from app.services.flow import FlowService

    from .conftest import FakeAudit, make_user

    calls = {"prefix": None, "dynamo": None, "db": None}

    class _Authz:
        async def assert_can_delete_project(self, pid, user):
            return None  # permitted

    class _Db:
        async def get_project(self, pid):
            return {"id": pid, "name": "Junk", "created_by": "u-x"}

        async def delete_project(self, pid):
            calls["db"] = pid
            return True

    class _Dynamo:
        async def delete_phase_states(self, pid):
            calls["dynamo"] = pid
            return 3

    class _Content:
        async def delete_prefix(self, prefix):
            calls["prefix"] = prefix
            return 7

    audit = FakeAudit()
    svc = FlowService.__new__(FlowService)
    svc._db, svc._dynamo, svc._audit, svc._authz, svc._content = _Db(), _Dynamo(), audit, _Authz(), _Content()

    res = await svc.delete_project("p9", make_user("SUPER_ADMIN"))
    assert res == {"projectId": "p9", "deleted": True, "filesRemoved": 7}
    assert calls == {"prefix": "content-store/p9", "dynamo": "p9", "db": "p9"}
    assert "project.deleted" in audit.events


async def test_delete_project_denied_without_authority():
    from app.domain.errors import SdlcError
    from app.services.flow import FlowService

    from .conftest import FakeAudit, make_user

    class _Authz:
        async def assert_can_delete_project(self, pid, user):
            raise SdlcError("FORBIDDEN", "nope")

    svc = FlowService.__new__(FlowService)
    svc._authz = _Authz()
    svc._audit = FakeAudit()
    with pytest.raises(SdlcError) as err:
        await svc.delete_project("p9", make_user("QA"))
    assert err.value.code == "FORBIDDEN"


async def test_plan_save_denied_without_write_permission():
    """only a stage writer (or PM-creator / SUPER_ADMIN) may edit the plan."""
    from app.domain.errors import SdlcError

    from .conftest import FakeAuthz, FakeDb, FakeWorkflow, make_user

    svc = ChatService.__new__(ChatService)
    svc._workflow = FakeWorkflow()
    svc._db = FakeDb()
    svc._authz = FakeAuthz({"u-QA": "QA"})  # QA is not a writer of stage 2 (SA)
    with pytest.raises(SdlcError) as err:
        await svc.save_plan(project_id="p1", phase=2, user=make_user("QA"),
                            overlay={"promptOverlay": "x", "referencedArtifactIds": [], "attachmentIds": [], "formworkIds": []})
    assert err.value.code == "FORBIDDEN"


async def test_can_write_stage_matrix():
    from .conftest import FakeAuthz, FakeDb, make_user

    svc = ChatService.__new__(ChatService)
    svc._db = FakeDb()
    svc._authz = FakeAuthz({"u-SA": "SA", "u-QA": "QA"})
    stage = {"reviewerRole": "SA", "writeRoles": ["SA"], "team": ["SA"]}
    assert await svc._can_write_stage("p1", stage, make_user("SUPER_ADMIN")) is True
    assert await svc._can_write_stage("p1", stage, make_user("SA")) is True
    assert await svc._can_write_stage("p1", stage, make_user("QA")) is False


async def test_can_write_stage_public_wrapper_resolves_by_phase():
    """the public can_write_stage resolves the stage by position first."""
    from .conftest import FakeAuthz, FakeDb, FakeWorkflow, make_user

    svc = ChatService.__new__(ChatService)
    svc._workflow = FakeWorkflow()
    svc._db = FakeDb()
    svc._authz = FakeAuthz({"u-SA": "SA", "u-QA": "QA"})
    assert await svc.can_write_stage("p1", 2, make_user("SA")) is True
    assert await svc.can_write_stage("p1", 2, make_user("QA")) is False


async def test_generate_validated_persists_validation_feedback(monkeypatch):
    """a give-up verdict's issues are surfaced as validation feedback rows,
    replacing any from a prior generation."""
    from .conftest import FakeDb

    settings = SimpleNamespace(VALIDATION_ENABLED=True, VALIDATION_MAX_REPAIRS=1)
    db = FakeDb()
    # a stale validation row from a previous run must be cleared
    await db.insert_feedback(project_id="p1", phase=2, source="validation",
                             category="old", severity="warning", comment="stale")

    async def fake_generate(deps, state, emit, *, rework=None):
        return _Out()

    async def fake_validate(deps, state, emit, out):
        return ValidationVerdict(
            ok=False,
            issues=[ValidationIssue(severity="error", area="mermaidArchitecture",
                                    problem="missing header", fix="add flowchart LR")],
            reworkInstructions="fix it",
        )

    monkeypatch.setattr(pa, "_generate", fake_generate)
    monkeypatch.setattr(pa, "_validate_output", fake_validate)
    deps = _deps(None, settings)
    deps.db = db
    await pa._generate_validated(deps, _state(), lambda e: None)

    rows = await db.list_feedback("p1", 2)
    validation = [r for r in rows if r["source"] == "validation"]
    assert len(validation) == 1  # stale row replaced
    assert validation[0]["category"] == "mermaidArchitecture"
    assert validation[0]["severity"] == "error"
    assert "missing header" in validation[0]["comment"]
    assert "add flowchart LR" in validation[0]["comment"]


async def test_generate_validated_clears_validation_feedback_when_clean(monkeypatch):
    """A passing verdict leaves no open validation issues (prior ones cleared)."""
    from .conftest import FakeDb

    settings = SimpleNamespace(VALIDATION_ENABLED=True, VALIDATION_MAX_REPAIRS=1)
    db = FakeDb()
    await db.insert_feedback(project_id="p1", phase=2, source="validation",
                             category="old", severity="warning", comment="stale")

    async def fake_generate(deps, state, emit, *, rework=None):
        return _Out()

    async def fake_validate(deps, state, emit, out):
        return ValidationVerdict(ok=True)

    monkeypatch.setattr(pa, "_generate", fake_generate)
    monkeypatch.setattr(pa, "_validate_output", fake_validate)
    deps = _deps(None, settings)
    deps.db = db
    await pa._generate_validated(deps, _state(), lambda e: None)

    rows = await db.list_feedback("p1", 2)
    assert [r for r in rows if r["source"] == "validation"] == []


async def test_generate_validated_disabled_skips_validation(monkeypatch):
    settings = SimpleNamespace(VALIDATION_ENABLED=False, VALIDATION_MAX_REPAIRS=1)
    called = {"validate": 0}

    async def fake_generate(deps, state, emit, *, rework=None):
        return _Out()

    async def fake_validate(deps, state, emit, out):
        called["validate"] += 1
        return ValidationVerdict(ok=True)

    monkeypatch.setattr(pa, "_generate", fake_generate)
    monkeypatch.setattr(pa, "_validate_output", fake_validate)
    await pa._generate_validated(_deps(None, settings), _state(), lambda e: None)
    assert called["validate"] == 0
