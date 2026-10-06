"""Multi-model routing: the role map, the failover chain, stage roles, rework escalation and the
super-admin API that edits it live."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.agents import phase_agents as pa
from app.agents.schemas import ValidationIssue, ValidationVerdict
from app.domain.errors import SdlcError
from app.domain.models import AgentState
from app.integrations import llm as llm_mod
from app.integrations.llm import LlmClient
from app.services import model_routes as mr

from .conftest import FakeAudit, FakeRedis, make_user


# ================================================================== the role map
def test_stage_roles_default_by_template_and_can_be_overridden():
    assert [mr.role_for_stage(t) for t in (1, 2, 3, 4, 5, 6, 7)] == [
        "generate", "reason", "reason", "generate", "generate", "generate", "generate"]
    assert mr.role_for_stage(1, "reason") == "reason" and mr.role_for_stage(2, "light") == "light"
    assert mr.role_for_stage(2, "bogus") == "reason" and mr.role_for_stage(2, None) == "reason"


def test_validate_routes_accepts_chains_and_rejects_anything_malformed():
    ok = mr.validate_routes({"reason": ["bedrock/us.anthropic.claude-opus-4-8", "bedrock/sonnet"],
                             "light": ["gemini/gemini-2.5-flash-lite"], "plan": []})
    assert ok == {"reason": ["bedrock/us.anthropic.claude-opus-4-8", "bedrock/sonnet"],
                  "light": ["gemini/gemini-2.5-flash-lite"]}
    for bad, why in [({"reasoning": ["bedrock/x"]}, "unknown role"), ({"light": ["haiku"]}, "not a valid model"),
                     ({"light": ["nope/model"]}, "unknown provider"), ({"light": ["bedrock/a b"]}, "not a valid model"),
                     ({"light": [f"bedrock/m{i}" for i in range(5)]}, "more than 4"), (["x"], "object")]:
        with pytest.raises(SdlcError, match=why):
            mr.validate_routes(bad)


def test_stored_routes_are_read_tolerantly_and_env_defaults_apply():
    raw = json.dumps({"light": ["bedrock/haiku", "bad", "bedrock/haiku"], "nonsense": ["bedrock/x"], "plan": "gemini/flash"})
    assert mr.parse_routes(raw) == {"light": ["bedrock/haiku"], "plan": ["gemini/flash"]}
    assert mr.parse_routes("{not json") == {} and mr.parse_routes(None) == {}
    assert mr.env_defaults(SimpleNamespace(LIGHT_MODEL="bedrock/h", PLAN_MODEL="")) == {"light": ["bedrock/h"]}


# ================================================================== the failover chain
class _Rec(LlmClient):
    def __init__(self, role_models=None, redis=None, fail=()):
        super().__init__("http://ai-client:8081", role_models=role_models, redis=redis)
        self.calls: list[str | None] = []
        self.fail = set(fail)

    async def _generate(self, *, model=None, **kw):
        self.calls.append(model)
        if model in self.fail:
            raise SdlcError("PROVIDER_ERROR", f"{model} unavailable")
        return llm_mod.LlmResult(provider="p", model=model or "default", content="{}",
                                 usage={"promptTokens": 1, "completionTokens": 1})

    async def _generate_stream(self, *, model=None, **kw):
        self.calls.append(f"stream:{model}")
        return llm_mod.LlmResult(provider="p", model=model or "default", content="x",
                                 usage={"promptTokens": 1, "completionTokens": 1})


async def _gen(c, **kw):
    return await c.generate(intent="standard", messages=[{"role": "user", "content": "x"}], **kw)


async def test_the_chain_is_tried_in_order_then_the_normal_chain():
    c = _Rec({"reason": ["bedrock/opus", "bedrock/sonnet", "gemini/pro"]}, fail={"bedrock/opus", "bedrock/sonnet"})
    res = await _gen(c, role="reason")
    assert c.calls == ["bedrock/opus", "bedrock/sonnet", "gemini/pro"] and res.model == "gemini/pro"

    c2 = _Rec({"reason": ["bedrock/opus"]}, fail={"bedrock/opus"})
    res = await _gen(c2, role="reason")
    assert c2.calls == ["bedrock/opus", None] and res.model == "default"          # normal chain last


async def test_a_failed_model_is_skipped_for_the_cooldown_per_model(monkeypatch):
    c = _Rec({"light": ["bedrock/typo", "bedrock/haiku"]}, fail={"bedrock/typo"})
    await _gen(c, role="light")
    await _gen(c, role="light")
    assert c.calls == ["bedrock/typo", "bedrock/haiku", "bedrock/haiku"]            # typo not retried inside the cooldown
    assert c.calls.count("bedrock/typo") == 1
    now = __import__("time").monotonic()
    monkeypatch.setattr(llm_mod.time, "monotonic", lambda: now + llm_mod.ROLE_COOLDOWN_SECONDS + 1)
    await _gen(c, role="light")
    assert c.calls[-2:] == ["bedrock/typo", "bedrock/haiku"]                         # tried again after the cooldown


async def test_roles_are_independent_and_an_explicit_model_wins():
    c = _Rec({"light": ["bedrock/haiku"], "reason": ["bedrock/opus"]})
    await _gen(c, role="light")
    await _gen(c, role="reason")
    await _gen(c, role="vision")                       # no route -> normal chain
    await _gen(c, role="light", model="groq/llama")    # explicit pin beats the role
    assert c.calls == ["bedrock/haiku", "bedrock/opus", None, "groq/llama"]


async def test_streaming_calls_are_routed_too():
    c = _Rec({"generate": ["bedrock/sonnet"]})
    await c.generate_stream(intent="generation", messages=[], on_delta=lambda t: None, role="generate")
    assert c.calls == ["stream:bedrock/sonnet"]


async def test_an_admins_live_routes_override_the_env_defaults_without_a_restart(monkeypatch):
    redis = FakeRedis()
    c = _Rec({"light": ["bedrock/env-haiku"]}, redis=redis)
    await _gen(c, role="light")
    assert c.calls == ["bedrock/env-haiku"]

    redis.kv["sdlc:settings:model_routes"] = json.dumps({"light": ["gemini/flash"], "reason": ["bedrock/opus"]})
    now = __import__("time").monotonic()
    monkeypatch.setattr(llm_mod.time, "monotonic", lambda: now + 6)                  # past the 5s cache
    await _gen(c, role="light")
    await _gen(c, role="reason")
    assert c.calls[1:] == ["gemini/flash", "bedrock/opus"]
    assert await c.route_chain("light") == ["gemini/flash"] and await c.has_route("reason")
    assert not await c.has_route("vision") and not await c.has_route(None)

    del redis.kv["sdlc:settings:model_routes"]                                       # admin clears it -> env default again
    monkeypatch.setattr(llm_mod.time, "monotonic", lambda: now + 12)
    assert await c.route_chain("light") == ["bedrock/env-haiku"]


async def test_a_damaged_live_setting_never_breaks_calls():
    redis = FakeRedis()
    redis.kv["sdlc:settings:model_routes"] = "{garbage"
    c = _Rec({}, redis=redis)
    assert (await _gen(c, role="light")).model == "default"


# ================================================================== stage routing + rework escalation
class _Part(BaseModel):
    alpha: str = "a"
    beta: str = "b"
    gamma: str = "c"


async def test_the_split_path_forwards_the_role_to_every_part_call():
    from .test_resume import PartDb

    seen: list[str | None] = []

    class Llm:
        async def generate_json(self, *, tag, schema, role=None, **_):
            seen.append(role)
            name = tag.rsplit(":", 1)[1]
            return schema(**{name: "v"}), SimpleNamespace(provider="p", model="m", attempts=[], tier="x", content="",
                                                          usage={"promptTokens": 1, "completionTokens": 1})

    deps = SimpleNamespace(llm=Llm(), db=PartDb(), settings=SimpleNamespace(PER_ARTIFACT_MAX_PARALLEL=2))
    st = AgentState(project_id="p", session_id="s", user_input="x", current_phase=2, stage_template=2)
    await pa._generate_phase_split(deps=deps, state=st, system="s", user="u", schema=_Part, base_tag="t",
                                   intent="x", max_tokens=10, model=None, emit=lambda e: None, role="reason")
    assert seen == ["reason"] * 3


def _st(template=4, role=""):
    return AgentState(project_id="p", session_id="s", current_phase=template, stage_template=template,
                      user_input="x", model_role=role)


async def _rework_roles(monkeypatch, *, template, role, has_reason):
    seen: list[str] = []

    async def fake_generate(deps, state, emit, *, rework=None):
        seen.append(state.model_role)
        return _Part()

    verdicts = iter([ValidationVerdict(ok=False, issues=[ValidationIssue(severity="error", area="intent", problem="x")],
                                       reworkInstructions="fix"), ValidationVerdict(ok=True)])

    async def fake_validate(deps, state, emit, out):
        return next(verdicts)

    class Llm:
        async def has_route(self, role):
            return has_reason and role == "reason"

    monkeypatch.setattr(pa, "_generate", fake_generate)
    monkeypatch.setattr(pa, "_validate_output", fake_validate)
    events: list[dict] = []
    deps = SimpleNamespace(llm=Llm(), audit=FakeAudit(), db=None,
                           settings=SimpleNamespace(VALIDATION_ENABLED=True, VALIDATION_MAX_REPAIRS=1))
    await pa._generate_validated(deps, _st(template, role), events.append)
    return seen, events


async def test_rework_escalates_to_the_reasoning_model_when_one_is_configured(monkeypatch):
    seen, events = await _rework_roles(monkeypatch, template=4, role="", has_reason=True)
    assert seen == ["", "reason"]                                   # first run as configured, retry on 'reason'
    assert any("retrying on the reasoning model" in e.get("label", "") for e in events)


async def test_no_escalation_without_a_reason_route_or_when_already_reasoning(monkeypatch):
    seen, _ = await _rework_roles(monkeypatch, template=4, role="", has_reason=False)
    assert seen == ["", ""]
    seen, events = await _rework_roles(monkeypatch, template=3, role="", has_reason=True)     # TA stage is 'reason' already
    assert seen == ["", ""] and not any("reasoning model" in e.get("label", "") for e in events)
    seen, _ = await _rework_roles(monkeypatch, template=4, role="reason", has_reason=True)
    assert seen == ["reason", "reason"]


def test_a_workflow_stage_can_choose_its_model_role():
    from app.services.workflow_v2 import StageConfig

    base = dict(key="a", name="Design", template=2, reviewerRole="SA", team=["SA"], inputs=["x"], outputs=["HLD"])
    assert StageConfig(**base).modelRole is None
    assert StageConfig(**base, modelRole="light").modelRole == "light"
    with pytest.raises(ValueError):
        StageConfig(**base, modelRole="vision")                      # only reason / generate / light are stage choices


# ================================================================== the admin API
class _Db:
    def __init__(self):
        self.settings: dict[str, str] = {}

    async def get_setting(self, key):
        return self.settings.get(key)

    async def set_setting(self, key, value, actor):
        self.settings[key] = value

    async def delete_setting(self, key):
        self.settings.pop(key, None)


def _container():
    redis = FakeRedis()

    async def delete(k):
        redis.kv.pop(k, None)

    redis.delete = delete

    class Llm:
        async def providers(self):
            return {"mode": "auto", "effectiveMock": False, "providers": [
                {"provider": "bedrock", "model": "us.anthropic.claude-sonnet", "configured": True, "breaker": "closed", "vision": True},
                {"provider": "gemini", "model": "gemini-2.5-flash", "configured": True, "breaker": "closed", "vision": True}]}

    return SimpleNamespace(db=_Db(), redis=redis, audit=FakeAudit(), llm=Llm(),
                           settings=SimpleNamespace(LIGHT_MODEL="bedrock/env-haiku", PLAN_MODEL=""))


async def test_admin_can_view_and_set_routes_and_they_apply_live():
    from app.api.project_routes import ModelRoutesRequest, get_model_routes, set_model_routes

    c, admin = _container(), make_user("SUPER_ADMIN")
    view = await get_model_routes(admin, c)
    roles = {r["role"]: r for r in view["roles"]}
    assert list(roles) == ["reason", "generate", "light", "plan", "vision"]
    assert roles["light"]["source"] == "env" and roles["light"]["effective"] == ["bedrock/env-haiku"]
    assert roles["reason"]["source"] == "default" and roles["reason"]["effective"] == []
    assert {m["id"] for m in view["catalog"]} == {"bedrock/us.anthropic.claude-sonnet", "gemini/gemini-2.5-flash"}
    assert view["stageDefaults"]["2"] == "reason" and view["maxChain"] == 4

    out = await set_model_routes(ModelRoutesRequest(routes={"reason": ["bedrock/opus", "bedrock/sonnet"],
                                                            "light": ["gemini/gemini-2.5-flash"]}), admin, c)
    stored = json.loads(c.db.settings["model_routes"])
    assert stored["reason"] == ["bedrock/opus", "bedrock/sonnet"]
    assert json.loads(c.redis.kv["sdlc:settings:model_routes"]) == stored            # mirrored for live use
    roles = {r["role"]: r for r in out["roles"]}
    assert roles["light"]["source"] == "admin" and roles["light"]["effective"] == ["gemini/gemini-2.5-flash"]
    assert roles["light"]["envDefault"] == ["bedrock/env-haiku"]
    assert "model_routes.changed" in c.audit.events

    cleared = await set_model_routes(ModelRoutesRequest(routes={}), admin, c)       # clearing reverts to env / default
    assert "model_routes" not in c.db.settings and "sdlc:settings:model_routes" not in c.redis.kv
    assert {r["role"]: r["source"] for r in cleared["roles"]}["light"] == "env"


async def test_only_a_super_admin_may_see_or_change_routing_and_bad_input_is_refused():
    from app.api.project_routes import ModelRoutesRequest, get_model_routes, set_model_routes

    c = _container()
    for fn, args in ((get_model_routes, ()), (set_model_routes, (ModelRoutesRequest(routes={}),))):
        with pytest.raises(SdlcError) as err:
            await fn(*args, make_user("PROJECT_MANAGER"), c)
        assert err.value.code == "FORBIDDEN"
    with pytest.raises(SdlcError, match="not a valid model"):
        await set_model_routes(ModelRoutesRequest(routes={"light": ["haiku"]}), make_user("SUPER_ADMIN"), c)
    assert "model_routes" not in c.db.settings


def test_a_stages_model_role_reaches_the_runtime_stage_dict():
    from app.services.workflow_v2 import StageConfig, WorkflowConfig, derive

    def stage(key, **kw):
        return StageConfig(key=key, name=f"Stage {key}", template=2, reviewerRole="SA", team=["SA"],
                           inputs=["x"], outputs=["HLD"], **kw)

    cfg = WorkflowConfig(stages=[stage("a", modelRole="light"), stage("b")])
    stages = derive(cfg)["stages"]
    assert [s.get("modelRole") for s in stages] == ["light", None]
    assert mr.role_for_stage(stages[0]["template"], stages[0].get("modelRole")) == "light"
    assert mr.role_for_stage(stages[1]["template"], stages[1].get("modelRole")) == "reason"
