from app.services.applicability import (
    GENERATED_ARTIFACTS, constraints_block, derive_traits, inapplicable_types, project_corpus, strip_scope_block,
)


def test_api_only_project_excludes_ui_automation_but_keeps_api_artifacts() -> None:
    traits = derive_traits(corpus="Payments REST API microservice on Spring Boot with PostgreSQL, deployed to AWS")
    assert (traits["ui"], traits["api"], traits["database"], traits["aws"], traits["service"]) == (
        False, True, True, True, True)
    out = inapplicable_types(traits, 4)
    assert set(out) == {"PLAYWRIGHT_SPEC"} and "no user interface" in out["PLAYWRIGHT_SPEC"]


def test_ui_project_keeps_playwright() -> None:
    traits = derive_traits(corpus="React web app with a REST backend")
    assert traits["ui"] is True and "PLAYWRIGHT_SPEC" not in inapplicable_types(traits, 4)


def test_explicit_negations_and_unknown_never_over_exclude() -> None:
    t = derive_traits(corpus="Stateless headless batch job, on-prem, no database")
    assert t["ui"] is False and t["database"] is False and t["cloud"] is False
    assert {"DBML", "CDK"} <= set(inapplicable_types(t, 3))
    unknown = derive_traits(corpus="Build me something useful")
    assert set(unknown.values()) == {None} and inapplicable_types(unknown, 4) == {}


def test_scope_block_is_not_read_as_project_traits() -> None:
    txt = "Internal tool\n\n## Production scope (confirmed by the reviewer)\n- Do NOT produce: DBML, CDK, OPENAPI."
    assert "DBML" not in strip_scope_block(txt)
    assert derive_traits(corpus=project_corpus(project={"name": "x"}, user_text=txt, upstream=[]))["api"] is None


def test_harness_extras_are_visible_to_planning_and_constraints_render() -> None:
    assert {"PLAYWRIGHT_SPEC", "REST_ASSURED", "JMETER_PLAN", "LOCUSTFILE"} <= set(GENERATED_ARTIFACTS[4])
    assert "PLAYWRIGHT_SPEC: no UI" in constraints_block({"PLAYWRIGHT_SPEC": "no UI"})
    assert constraints_block({}) == ""


# ---- plan review: applicability + shared state across tabs/sessions -----------------
import asyncio  # noqa: E402
import json  # noqa: E402

from app.services.chat import ChatService  # noqa: E402


def _chat() -> ChatService:
    return ChatService.__new__(ChatService)


def test_plan_drops_inapplicable_artifacts_whatever_the_llm_said() -> None:
    chat = _chat()
    intel = {"willProduce": [
        {"output": "PLAYWRIGHT_SPEC", "recommended": True, "include": True, "reason": "llm said yes"},
        {"output": "REST_ASSURED", "recommended": True, "include": True, "reason": "ok"},
    ]}
    out = chat._apply_applicability(intel, {"PLAYWRIGHT_SPEC": "no user interface", "DBML": "no store"})
    assert [a["output"] for a in out["willProduce"]] == ["REST_ASSURED"]   # not part of the plan at all
    assert len(out["promptChecks"]) == 2


class _Redis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    async def set(self, k, v, nx=False, ex=None):
        if nx and k in self.kv:
            return None
        self.kv[k] = v
        return True

    async def get(self, k): return self.kv.get(k)
    async def delete(self, k): self.kv.pop(k, None)
    async def exists(self, k): return int(k in self.kv)


async def test_second_tab_waits_for_the_plan_instead_of_rebuilding_and_state_is_shared() -> None:
    from types import SimpleNamespace

    chat = _chat()
    chat._redis = _Redis()
    chat._settings = SimpleNamespace(INTELLIGENT_PLANNING=True)
    chat._authz = SimpleNamespace(assert_project_access=lambda *a: _noop())
    computes: list[int] = []

    async def fake_compute(**kw):
        computes.append(1)
        await asyncio.sleep(0.2)
        await chat._redis.set(kw["ckey"], json.dumps({"sig": kw["sig"], "plan": {"understood": "u"}}))
        return {"understood": "u", "cached": False}

    chat._compute_intelligent_plan = fake_compute
    kwargs = dict(project={"id": "p", "tech_stack": "x"}, phase=4, stage={"template": 4, "outputs": []},
                  overlay={}, available_tools=[], skills=[], prior_arts=[], canon_applied=False)
    a = asyncio.create_task(chat._intelligent_plan(**kwargs))
    await asyncio.sleep(0.05)
    state = await chat.plan_state(project_id="p", phase=4, user=None)
    assert state == {"building": True, "ready": False}           # visible to another tab
    b = await chat._intelligent_plan(**kwargs)                   # second tab: waits, no 2nd LLM call
    assert (await a)["understood"] == "u" and b["cached"] is True and len(computes) == 1
    assert await chat.plan_state(project_id="p", phase=4, user=None) == {"building": False, "ready": True}


async def _noop() -> None:
    return None


# ---- every stage, not just QA ---------------------------------------------------------
def test_every_stage_gets_its_own_applicability_rules() -> None:
    api_serverless = derive_traits(corpus="Orders REST API on Azure with Terraform, serverless functions, no database")
    s2, s3 = inapplicable_types(api_serverless, 2), inapplicable_types(api_serverless, 3)
    s4, s5 = inapplicable_types(api_serverless, 4), inapplicable_types(api_serverless, 5)
    assert "CLOUDCRAFT_JSON" in s2                                     # stage 2: AWS topology, not AWS
    assert {"CDK", "DBML"} <= set(s3) and "OPENAPI" not in s3          # stage 3: API stays
    assert "PLAYWRIGHT_SPEC" in s4 and "REST_ASSURED" not in s4        # stage 4
    assert {"DOCKERFILE", "AWS_SECRETS_CHECK"} <= set(s5)              # stage 5: serverless, not AWS


def test_library_has_no_runtime_service_artifacts_but_keeps_core_ones() -> None:
    t = derive_traits(corpus="Python SDK library published as a package, no UI")
    gone = {x for n in range(1, 7) for x in inapplicable_types(t, n)}
    assert {"K6_SCRIPT", "JMETER_PLAN", "LOCUSTFILE", "GRAFANA_DASHBOARD", "ZAP_SCAN", "PLAYWRIGHT_SPEC"} <= gone
    core = {"PRD", "HLD", "LLD", "TEST_STRATEGY", "GITHUB_ACTIONS", "APP_CODE", "UNIT_TESTS", "PULL_REQUEST"}
    assert not core & gone


def test_a_full_stack_aws_project_excludes_nothing() -> None:
    t = derive_traits(corpus="React web app with REST API, PostgreSQL, Docker on AWS ECS")
    assert all(inapplicable_types(t, n) == {} for n in range(1, 7))


def test_tools_are_gated_with_their_artifacts() -> None:
    from app.services.applicability import TOOL_ARTIFACT
    t = derive_traits(corpus="REST API only service on AWS")
    gone = inapplicable_types(t, 4)
    assert [x for x, a in TOOL_ARTIFACT.items() if a in gone] == ["playwright_generate_tests", "playwright_run_tests"]


def test_generation_skips_inapplicable_fields_without_any_explicit_scope() -> None:
    from app.agents.phase_agents import _scope_ctx, run_scope, scope_skipped_fields
    from app.agents.schemas import PHASE_SCHEMAS
    from app.domain.models import AgentState

    st = AgentState(project_id="p", session_id="s", current_phase=3, stage_template=3,
                    user_input="Design the orders service", tech_stack="Python FastAPI REST API on Azure, stateless")
    token = _scope_ctx.set(run_scope(st))
    try:
        skipped = set(scope_skipped_fields(st, list(PHASE_SCHEMAS[3].model_fields)))
    finally:
        _scope_ctx.reset(token)
    assert skipped == {"cdkStack", "dbmlSchema"}          # AWS CDK + DBML; OpenAPI and the LLD remain
    # an explicit reviewer request always wins over inference
    st2 = st.model_copy(update={"user_input": "x\n\n## Production scope (confirmed by the reviewer)\n"
                                              "- Produce ONLY these artifacts: CDK, LLD.\n"})
    assert "CDK" not in run_scope(st2)["exclude"]


# ---- LLM decides, code enforces ------------------------------------------------------
from types import SimpleNamespace  # noqa: E402

from app.services.applicability import resolve_traits, trait_values  # noqa: E402


def J(value, conf=0.9, ev="quoted"):
    return {"value": value, "confidence": conf, "evidence": ev}


def test_precedence_override_beats_ai_beats_rules_and_unknown_never_excludes() -> None:
    llm = {"ui": J("absent"), "api": J("present"), "database": J("absent", conf=0.4),
           "cloud": J("unknown", conf=0.0), "aws": J("unknown"), "container": J("present"), "service": J("unknown")}
    rules = {"ui": True, "api": False, "database": True, "cloud": True, "aws": True, "container": False}
    d = resolve_traits(llm, {"ui": "present"}, rules)
    assert (d["ui"]["value"], d["ui"]["source"]) == (True, "override")           # human wins over AI
    assert (d["api"]["value"], d["api"]["source"]) == (True, "ai")               # AI wins over rules
    assert d["database"]["value"] is None                                         # low confidence = unknown
    assert d["cloud"]["value"] is None and d["aws"]["value"] is None              # NOT back-filled by regex
    assert d["service"]["value"] is True and d["service"]["source"] == "derived"  # api present ⇒ service
    assert resolve_traits(None, {}, rules)["ui"]["source"] == "rules"             # AI unavailable ⇒ rules


class FakeDb:
    def __init__(self) -> None:
        self.row, self.overrides = None, {}

    async def get_stage_traits(self, *_): return self.row
    async def upsert_stage_traits(self, pid, phase, sig, tj): self.row = {"sig": sig, "traits_json": tj}
    async def get_trait_overrides(self, *_): return self.overrides


class FakeLlm:
    def __init__(self, result=None, fail=False) -> None:
        self.calls, self.result, self.fail = 0, result, fail

    async def generate_json(self, *, schema, **_):
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider down")
        return schema(**self.result), None


API_ONLY = {"projectType": "REST API", "ui": J("absent", ev="REST-only microservice"), "api": J("present"),
            "database": J("present"), "cloud": J("present"), "aws": J("present"), "container": J("present"),
            "service": J("present")}


def _svc(llm, db):
    chat = ChatService.__new__(ChatService)
    chat._db, chat._deps, chat._settings = db, SimpleNamespace(llm=llm), SimpleNamespace(INTELLIGENT_PLANNING=True)
    return chat


async def test_ai_judges_once_plan_and_generation_read_the_same_answer() -> None:
    db, llm = FakeDb(), FakeLlm(API_ONLY)
    chat = _svc(llm, db)
    kw = dict(project={"id": "p", "name": "Orders", "tech_stack": "Java"}, phase=4, user_text="Add tests", upstream=[])
    shown = await chat.resolve_project_traits(**kw, allow_llm=True)             # plan display
    assert llm.calls == 1 and shown["ui"]["value"] is False and shown["ui"]["evidence"] == "REST-only microservice"
    run = await chat.resolve_project_traits(**{**kw, "user_text": "edited later"}, allow_llm=False)   # trigger
    assert llm.calls == 1 and trait_values(run) == trait_values(shown)          # no new call, same decision
    assert "PLAYWRIGHT_SPEC" in inapplicable_types({k: v for k, v in trait_values(run).items() if k[0] != "_"}, 4)


async def test_override_wins_and_ai_failure_falls_back_to_rules() -> None:
    db = FakeDb()
    db.overrides = {"ui": "present"}
    chat = _svc(FakeLlm(API_ONLY), db)
    d = await chat.resolve_project_traits(project={"id": "p", "name": "x"}, phase=4, user_text="", upstream=[],
                                          allow_llm=True)
    assert d["ui"]["value"] is True and "PLAYWRIGHT_SPEC" not in inapplicable_types(trait_values(d), 4)
    down = _svc(FakeLlm(fail=True), FakeDb())
    d2 = await down.resolve_project_traits(project={"id": "p", "name": "x", "tech_stack": "REST API"}, phase=4,
                                           user_text="", upstream=[], allow_llm=True)
    assert d2["ui"]["source"] == "rules" and d2["ui"]["value"] is False       # graceful fallback


def test_generation_uses_the_resolved_traits_not_its_own_guess() -> None:
    from app.agents.phase_agents import run_scope
    from app.domain.models import AgentState

    st = AgentState(project_id="p", session_id="s", current_phase=4, stage_template=4,
                    user_input="build a React web app with Playwright", tech_stack="React",
                    project_traits={"ui": False, "api": True, "service": True})
    assert "PLAYWRIGHT_SPEC" in run_scope(st)["exclude"]        # the AI/override decision is enforced
    st2 = st.model_copy(update={"project_traits": {}})
    assert "PLAYWRIGHT_SPEC" not in run_scope(st2)["exclude"]   # no resolved traits ⇒ keyword rules
