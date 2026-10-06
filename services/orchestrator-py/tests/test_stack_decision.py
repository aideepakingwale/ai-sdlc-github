"""The technology stack is decided by the Technical Architect stage, not asked at
project creation: undecided projects say so in every prompt, the TA stage asks
when nothing states a stack, records what it decides, and never overwrites a
stack a manager set by hand."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agents.prompts import build_phase_prompt, render_stack
from app.domain.errors import SdlcError
from app.domain.models import ContextArtifact, CreateProjectRequest, SetTechStackRequest
from app.services import stack as st
from app.services.chat import ChatService
from app.services.tech_catalog import compose_stack

from .conftest import FakeAudit, FakeDb, make_user


# ---------------------------------------------------------------- creation
def test_creating_a_project_needs_no_stack():
    req = CreateProjectRequest(name="Payments")
    assert req.techStack == "" and req.language is None and req.frameworks == []
    assert compose_stack(req.language, req.languageVersion, req.frameworks, fallback=req.techStack) == ""


def test_legacy_structured_stack_still_composes():
    assert compose_stack("Python", "3.12", ["FastAPI"]) == "Python 3.12 + FastAPI"


async def test_create_route_stores_an_undecided_stack():
    from types import SimpleNamespace

    from app.api.project_routes import create_project

    db, audit = FakeDb(), FakeAudit()
    container = SimpleNamespace(
        db=db, audit=audit, workflow=None,
        authz=SimpleNamespace(assert_can_create_project=lambda user: None),
    )
    out = await create_project(CreateProjectRequest(name="Payments"), make_user("PROJECT_MANAGER"), container)
    project = out["project"]
    assert project["techStack"] == "" and project["techStackDecided"] is False
    assert db.projects[project["id"]]["tech_stack"] == ""


# ---------------------------------------------------------------- prompts
def test_undecided_prompts_stay_neutral_and_never_assume_a_language():
    other = render_stack("", owner=False)
    assert "NOT DECIDED YET" in other and "technology-neutral" in other
    assert "Node.js" not in other and "Python" not in other
    assert "do NOT default to AWS" in other  # the platform rule still applies


def test_technical_architect_is_told_to_decide_and_record_it():
    owner = render_stack("", owner=True)
    assert "YOU decide it" in owner and "Technology stack decision" in owner and "none yet" in owner


def test_a_decided_stack_pins_later_stages_but_the_ta_may_revise_its_own():
    assert "Target technology stack: Go 1.22 + Gin" in render_stack("Go 1.22 + Gin", owner=False, source="ta")
    revisit = render_stack("Go 1.22 + Gin", owner=True, source="ta")
    assert "Currently recorded: Go 1.22 + Gin" in revisit and "KEEP it" in revisit
    # a stack a manager pinned is binding even for the Technical Architect
    assert "Target technology stack: Go 1.22 + Gin" in render_stack("Go 1.22 + Gin", owner=True, source="user")


def test_phase_prompt_uses_the_undecided_text_for_every_phase_but_pins_the_ta_decision():
    for phase in (1, 2, 4, 5, 6):
        system, _ = build_phase_prompt(phase=phase, context_block="", rag_block="", user_input="x",
                                       amend_comments=None)
        assert "NOT DECIDED YET" in system
    ta, _ = build_phase_prompt(phase=3, context_block="", rag_block="", user_input="x", amend_comments=None)
    assert "YOU decide it" in ta
    dev, _ = build_phase_prompt(phase=6, context_block="", rag_block="", user_input="x", amend_comments=None,
                                tech_stack="Java 21 + Spring Boot", tech_stack_source="ta")
    assert "Target technology stack: Java 21 + Spring Boot" in dev


def test_stack_owner_detection():
    assert st.is_stack_owner(template=3)
    assert st.is_stack_owner(persona="Technical Architect")
    assert not st.is_stack_owner(template=2, persona="Solution Architect")


# ---------------------------------------------------------------- detection
@pytest.mark.parametrize("text,expected", [
    ("Build it with Python and FastAPI", True),
    ("We use Spring Boot on Java 21", True),
    ("A .NET service", True),
    ("Let's go live next quarter and express our thanks", False),
    ("The order management system must support refunds", False),
    ("", False),
])
def test_mentions_stack(text, expected):
    assert st.mentions_stack(text) is expected


def test_infer_stack_picks_the_dominant_catalog_language():
    dec = st.infer_stack("The service is written in Java 21 using Spring Boot; Spring Boot actuator; "
                         "one Python script for data loading")
    assert dec and dec.language == "Java" and "Spring Boot" in dec.frameworks and dec.languageVersion == "21 (LTS)"
    assert st.infer_stack("nothing technical here") is None


# ---------------------------------------------------------------- clarification gate
def _svc():
    from types import SimpleNamespace
    svc = ChatService.__new__(ChatService)
    svc._settings = SimpleNamespace(CLARIFY_MAX_QUESTIONS=6)
    return svc


def _ask(svc, *, stage, project=None, user_input="Build an order system", context=None, extra="", qs=None):
    return svc._with_stack_question(
        qs or [], project=project or {"id": "p", "tech_stack": ""}, stage=stage,
        user_input=user_input, context=context or [], extra_context=extra)


TA = {"template": 3, "persona": "Technical Architect", "name": "Technical Design"}


def test_ta_asks_for_the_stack_when_nothing_states_one():
    (q,) = _ask(_svc(), stage=TA)
    assert q["question"] == st.STACK_QUESTION and q["id"] == "tech-stack"
    labels = [o["label"] for o in q["options"]]
    assert labels[0] == st.RECOMMEND_OPTION and any(label.startswith("Python") for label in labels)


def test_other_stages_never_ask_for_the_stack():
    for stage in ({"template": 1, "persona": "Product Owner"}, {"template": 2, "persona": "Solution Architect"},
                  {"template": 6, "persona": "Senior Developer"}):
        assert _ask(_svc(), stage=stage) == []


def test_no_question_when_the_stack_is_decided_or_stated_anywhere():
    svc = _svc()
    assert _ask(svc, stage=TA, project={"id": "p", "tech_stack": "Go 1.22"}) == []
    assert _ask(svc, stage=TA, user_input="Use Kotlin") == []
    assert _ask(svc, stage=TA, extra="### Attachment - spec.docx\nThe API is Node.js based") == []
    art = ContextArtifact(phase=2, type="HLD", title="HLD", summary="s", content="Backend: Java microservices")
    assert _ask(svc, stage=TA, context=[art]) == []


def test_the_stack_question_is_never_asked_twice():
    answered = f"## Clarifications (confirmed by the reviewer)\n- {st.STACK_QUESTION}\n  → {st.RECOMMEND_OPTION}"
    assert _ask(_svc(), stage=TA, user_input=answered) == []


def test_models_own_language_question_is_not_duplicated():
    own = [{"id": "lang", "question": "Which programming language do you prefer?", "header": "Language", "options": []}]
    assert _ask(_svc(), stage=TA, qs=own) == own


def test_stack_question_respects_the_question_cap():
    qs = [{"id": f"q{i}", "question": f"Question {i}?", "header": "", "options": []} for i in range(9)]
    out = _ask(_svc(), stage=TA, qs=qs)
    assert len(out) == 6 and out[0]["id"] == "tech-stack"


# ---------------------------------------------------------------- decision parsing
LLD = """# Low-Level Design

Intro text.

## Technology stack decision

**Stack:** Python 3.12 + FastAPI | PostgreSQL 16

Rationale: the team knows Python; async IO suits the workload.
Alternatives rejected: Java (heavier), Go (smaller hiring pool).

## Components
"""


def test_explicit_decision_line_is_parsed_verbatim():
    dec = st.parse_decision_section(LLD)
    assert dec and dec.compose() == "Python 3.12 + FastAPI | PostgreSQL 16" and dec.datastore == "PostgreSQL 16"


@pytest.mark.parametrize("section", [
    "## Technology Stack Decision\n- **Stack**: Go 1.22 + Gin",
    "### Technology stack decision\n> Stack: Java 21 (LTS) + Spring Boot",
    "# Technology stack decision\n_Stack:_ Rust 1.79 + Axum",
])
def test_decision_line_tolerates_formatting_variants(section):
    dec = st.parse_decision_section(section)
    assert dec and dec.compose().split()[0] in ("Go", "Java", "Rust")


@pytest.mark.parametrize("text", [
    "no section here, but Python is mentioned",
    "## Technology stack decision\n\n**Stack:** TBD",
    "## Technology stack decision\n\nWe will decide later.",
    "## Components\n**Stack:** Python 3.12",                     # a Stack line outside the section is not a decision
])
def test_missing_or_placeholder_decisions_are_not_parsed(text):
    assert st.parse_decision_section(text) is None


def test_decide_prefers_the_explicit_line_then_falls_back_to_inference():
    assert st.decide_from_text("Java everywhere, Java Java", LLD).compose() == "Python 3.12 + FastAPI | PostgreSQL 16"
    assert st.decide_from_text("The service is Go with Gin, Gin routes, golang modules").compose().startswith("Go")
    assert st.decide_from_text("purely generic prose") is None


# ---------------------------------------------------------------- capture
class _Deps:
    def __init__(self, project):
        self.db, self.audit = FakeDb(), FakeAudit()
        self.db.projects[project["id"]] = project


def _state(pid="p1"):
    from app.domain.models import AgentState
    return AgentState(project_id=pid, session_id="s", current_phase=3, stage_template=3, user_input="x")


async def test_ta_decision_is_recorded_on_the_project_and_audited():
    from app.agents.phase_agents import _capture_stack

    deps = _Deps({"id": "p1", "tech_stack": "", "tech_stack_source": ""})
    emitted = []
    await _capture_stack(deps, _state(), emitted.append, texts=[LLD])
    row = deps.db.projects["p1"]
    assert row["tech_stack"] == "Python 3.12 + FastAPI | PostgreSQL 16" and row["tech_stack_source"] == "ta"
    assert "project.stack_decided" in deps.audit.events
    assert any("Technology stack decided" in e.get("label", "") for e in emitted)


async def test_ta_falls_back_to_inferring_from_the_design_text():
    from app.agents.phase_agents import _capture_stack

    deps = _Deps({"id": "p1", "tech_stack": "", "tech_stack_source": ""})
    await _capture_stack(deps, _state(), lambda e: None, texts=["The service uses Go and Gin, Gin routes, Go modules"])
    assert deps.db.projects["p1"]["tech_stack"].startswith("Go")


async def test_ta_leaves_the_stack_undecided_when_it_cannot_tell():
    from app.agents.phase_agents import _capture_stack

    deps = _Deps({"id": "p1", "tech_stack": "", "tech_stack_source": ""})
    emitted = []
    await _capture_stack(deps, _state(), emitted.append, texts=["generic prose"])
    assert deps.db.projects["p1"]["tech_stack"] == ""
    assert any("No technology stack could be determined" in e.get("label", "") for e in emitted)


async def test_ta_never_overwrites_a_manual_stack_but_may_revise_its_own():
    from app.agents.phase_agents import _capture_stack

    new = "## Technology stack decision\n**Stack:** Rust 1.79 + Axum"
    manual = _Deps({"id": "p1", "tech_stack": "Java 21", "tech_stack_source": "user"})
    await _capture_stack(manual, _state(), lambda e: None, texts=[new])
    assert manual.db.projects["p1"]["tech_stack"] == "Java 21"

    own = _Deps({"id": "p1", "tech_stack": "Go 1.22", "tech_stack_source": "ta"})
    await _capture_stack(own, _state(), lambda e: None, texts=[new])
    assert own.db.projects["p1"]["tech_stack"] == "Rust 1.79 + Axum"


async def test_a_failing_database_never_fails_the_stage():
    from app.agents.phase_agents import _capture_stack

    class _Boom:
        async def get_project(self, pid):
            raise RuntimeError("db down")

    deps = SimpleNamespace(db=_Boom(), audit=FakeAudit())
    await _capture_stack(deps, _state(), lambda e: None, texts=[LLD])      # must not raise


def test_the_ta_prompt_asks_for_the_parseable_decision_section():
    owner = render_stack("", owner=True)
    assert "## Technology stack decision" in owner and "**Stack:** <language and version>" in owner


# ---------------------------------------------------------------- manual override route
async def test_manager_can_set_and_clear_the_stack_and_others_cannot():
    from types import SimpleNamespace

    from app.api.project_routes import set_tech_stack

    db, audit = FakeDb(), FakeAudit()
    db.projects["p1"] = {"id": "p1", "name": "P", "created_by": "u-PROJECT_MANAGER", "tech_stack": "",
                         "tech_stack_source": ""}

    class _Authz:
        async def assert_project_access(self, pid, user):
            return None

        async def get_membership_role(self, pid, uid):
            return "QA" if uid == "u-QA" else ("TA" if uid == "u-TA" else None)

    container = SimpleNamespace(db=db, audit=audit, authz=_Authz())
    pm = make_user("PROJECT_MANAGER")
    out = await set_tech_stack("p1", SetTechStackRequest(language="Java", languageVersion="21 (LTS)",
                                                         frameworks=["Spring Boot"]), pm, container)
    assert out["techStack"] == "Java 21 (LTS) + Spring Boot" and db.projects["p1"]["tech_stack_source"] == "user"

    with pytest.raises(SdlcError) as err:
        await set_tech_stack("p1", SetTechStackRequest(language="Go"), make_user("QA"), container)
    assert err.value.code == "FORBIDDEN"

    await set_tech_stack("p1", SetTechStackRequest(language="Go", languageVersion="1.22"), make_user("TA"), container)
    assert db.projects["p1"]["tech_stack"] == "Go 1.22"

    cleared = await set_tech_stack("p1", SetTechStackRequest(), pm, container)
    assert cleared["techStackDecided"] is False and db.projects["p1"]["tech_stack_source"] == ""


# ---------------------------------------------------------------- the real TA stage records it
async def test_running_the_technical_design_stage_records_the_decided_stack(monkeypatch):
    from app.agents import phase_agents as pa
    from app.agents.schemas import Phase3Output

    out = Phase3Output(
        lldMarkdown=LLD, plantumlDiagrams=["@startuml\n@enduml"], mermaidSequence="sequenceDiagram\n A->>B: hi",
        openapiYaml="openapi: 3.0.0", dbmlSchema="Table t {}", cdkStack="// cdk")

    async def fake_generate(deps, state, emit):
        return out

    async def fake_tool(deps, emit, name, args):
        return {"result": "PASS", "violations": []}

    async def fake_publish(deps, emit, name, args):
        return {"url": "u", "htmlUrl": "h"}

    async def fake_save(deps, state, emit, **kw):
        return ContextArtifact(phase=3, type=kw["type_"], title=kw["title"], summary="s")

    async def none(*a, **kw):
        return None

    monkeypatch.setattr(pa, "_generate_validated", fake_generate)
    monkeypatch.setattr(pa, "_tool", fake_tool)
    monkeypatch.setattr(pa, "_publish", fake_publish)
    monkeypatch.setattr(pa, "_save_artifact", fake_save)
    monkeypatch.setattr(pa, "_save_architecture_svg", none)
    monkeypatch.setattr(pa, "_save_architecture_drawio", none)

    deps = _Deps({"id": "p1", "tech_stack": "", "tech_stack_source": ""})
    result = await pa._run_phase3(deps, _state(), lambda e: None)
    assert result.gate_status == "PENDING_REVIEW"
    assert deps.db.projects["p1"]["tech_stack"] == "Python 3.12 + FastAPI | PostgreSQL 16"
    assert deps.db.projects["p1"]["tech_stack_source"] == "ta"
