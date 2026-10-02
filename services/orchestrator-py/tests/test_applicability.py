from app.services.applicability import (
    GENERATED_ARTIFACTS, constraints_block, derive_traits, inapplicable_types, project_corpus, strip_scope_block,
)


def test_api_only_project_excludes_ui_automation_but_keeps_api_artifacts() -> None:
    traits = derive_traits(corpus="Payments REST API microservice on Spring Boot with PostgreSQL, deployed to AWS")
    assert traits == {"ui": False, "api": True, "database": True, "cloud": True}
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


def test_plan_marks_inapplicable_artifacts_not_recommended_whatever_the_llm_said() -> None:
    chat = _chat()
    intel = {"willProduce": [
        {"output": "PLAYWRIGHT_SPEC", "recommended": True, "include": True, "reason": "llm said yes"},
        {"output": "REST_ASSURED", "recommended": True, "include": True, "reason": "ok"},
    ]}
    out = chat._apply_applicability(intel, {"PLAYWRIGHT_SPEC": "no user interface", "DBML": "no store"})
    by = {a["output"]: a for a in out["willProduce"]}
    assert by["PLAYWRIGHT_SPEC"]["recommended"] is False and by["PLAYWRIGHT_SPEC"]["include"] is False
    assert by["PLAYWRIGHT_SPEC"]["reason"].startswith("Not applicable")
    assert by["REST_ASSURED"]["recommended"] is True            # untouched
    assert by["DBML"]["recommended"] is False                    # listed even if the LLM omitted it
    assert len(out["promptChecks"]) == 2


def test_applicability_reads_project_config_not_the_scope_block() -> None:
    chat = _chat()
    project = {"name": "Orders", "tech_stack": "Java Spring Boot REST API, PostgreSQL", "github_repo": "x/y"}
    out = chat._applicability(project=project, stage={"template": 4},
                              overlay={"promptOverlay": "Add tests"}, prior_arts=[], attachments=[])
    assert list(out) == ["PLAYWRIGHT_SPEC"]


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
