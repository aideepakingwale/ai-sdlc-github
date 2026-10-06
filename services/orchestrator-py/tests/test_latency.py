"""Latency work: light calls routed to a fast model (with safe fallback), validator rework
limited to the artifacts it flagged, no planner-node LLM call, parallel context compression."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.agents import phase_agents as pa
from app.agents.schemas import ValidationIssue, ValidationVerdict
from app.domain.errors import SdlcError
from app.domain.models import AgentState, ContextArtifact
from app.integrations import llm as llm_mod
from app.integrations.llm import LlmClient


# ================================================================== role-based model routing
class _Recorder(LlmClient):
    """LlmClient whose network call is replaced; records the model each call asked for."""

    def __init__(self, role_models, fail_on=None):
        super().__init__("http://ai-client:8081", role_models=role_models)
        self.models: list[str | None] = []
        self.fail_on = fail_on

    async def _generate(self, *, model=None, **kw):
        self.models.append(model)
        if self.fail_on and model == self.fail_on:
            raise SdlcError("PROVIDER_ERROR", "model not found")
        return llm_mod.LlmResult(provider="p", model=model or "default", content='{"ok": true}',
                                 usage={"promptTokens": 1, "completionTokens": 1})


async def _call(c, **kw):
    return await c.generate(intent="standard", messages=[{"role": "user", "content": "x"}], **kw)


async def test_light_role_uses_the_configured_fast_model():
    c = _Recorder({"light": "bedrock/haiku", "plan": "bedrock/plan-model"})
    await _call(c, role="light")
    await _call(c, role="plan")
    await _call(c)                                        # no role -> normal chain
    assert c.models == ["bedrock/haiku", "bedrock/plan-model", None]


async def test_an_explicit_model_beats_the_role_and_unset_roles_change_nothing():
    c = _Recorder({"light": "bedrock/haiku"})
    await _call(c, role="light", model="groq/llama")
    assert c.models == ["groq/llama"]
    plain = _Recorder({})
    await _call(plain, role="light")
    assert plain.models == [None]


async def test_a_failing_fast_model_falls_back_once_then_is_bypassed_for_the_cooldown(monkeypatch):
    c = _Recorder({"light": "bedrock/typo"}, fail_on="bedrock/typo")
    res = await _call(c, role="light")                    # pinned fails -> retried on the normal chain
    assert c.models == ["bedrock/typo", None] and res.model == "default"
    await _call(c, role="light")                          # inside the cooldown: no wasted attempt
    assert c.models == ["bedrock/typo", None, None]
    now = __import__("time").monotonic()
    monkeypatch.setattr(llm_mod.time, "monotonic", lambda: now + llm_mod.ROLE_COOLDOWN_SECONDS + 1)
    await _call(c, role="light")                          # cooldown over: tries the fast model again
    assert c.models[3] == "bedrock/typo"


async def test_only_provider_errors_trigger_the_fallback():
    class Boom(_Recorder):
        async def _generate(self, **kw):
            raise SdlcError("VALIDATION_FAILED", "bad request")

    with pytest.raises(SdlcError, match="bad request"):
        await _call(Boom({"light": "bedrock/haiku"}), role="light")


async def test_generate_json_passes_the_role_through():
    class Out(BaseModel):
        ok: bool

    c = _Recorder({"light": "bedrock/haiku"})
    data, _ = await c.generate_json(intent="standard", messages=[{"role": "user", "content": "x"}],
                                    schema=Out, role="light")
    assert data.ok and c.models == ["bedrock/haiku"]


def test_every_light_call_site_is_marked():
    """The judging / summarising calls must carry role='light' (planner proposal: role='plan')."""
    import pathlib
    root = pathlib.Path(pa.__file__).parents[1]
    expect = {
        "graph/pipeline.py": 'tag="fact_check_node"', "services/context.py": 'tag="context_compression"',
        "services/chat.py": 'tag="clarify"', "agents/phase_agents.py": "validation_stage",
    }
    for rel, anchor in expect.items():
        text = (root / rel).read_text()
        i = text.index(anchor)
        assert 'role="light"' in text[i - 200:i + 400], f"{rel}: {anchor} is not routed to the light model"
    assert 'role="light"' in (root / "services/chat.py").read_text().split('tag="project_traits"')[1][:200]
    assert 'role="plan"' in (root / "services/chat.py").read_text().split("schema=StagePlanIntel")[1][:100]


def test_defaults_are_safe_and_workers_increased():
    from app.config import Settings

    fields = Settings.model_fields
    assert fields["LIGHT_MODEL"].default == "" and fields["PLAN_MODEL"].default == ""
    assert fields["GENERATION_WORKERS"].default == 4 and fields["VALIDATION_LOCALISED_REWORK"].default is True


# ================================================================== localised validator rework
class _Out3(BaseModel):
    lldMarkdown: str = "lld"
    components: list[str] = []
    openapiYaml: str = "openapi: 3.0.0"
    dbmlSchema: str = "Table t {}"
    cdkStack: str = "//"


def _state3() -> AgentState:
    return AgentState(project_id="p", session_id="s", current_phase=3, stage_template=3, user_input="design it")


def _errs(*areas: str) -> list[ValidationIssue]:
    return [ValidationIssue(severity="error", area=a, problem=f"{a} is wrong") for a in areas]


def test_issues_are_localised_to_fields_by_name_or_artifact_type():
    out, st = _Out3(), _state3()
    assert pa.failing_fields(st, out, _errs("openapiYaml")) == ["openapiYaml"]
    assert pa.failing_fields(st, out, _errs("OpenAPI contract")) == ["openapiYaml"]      # type, in free text
    assert pa.failing_fields(st, out, _errs("DBML", "openapiYaml")) == ["openapiYaml", "dbmlSchema"]
    assert pa.failing_fields(st, out, _errs("LLD")) == ["lldMarkdown", "components"]      # a type covers several fields


def test_unlocalisable_or_total_issues_mean_a_full_regeneration():
    out, st = _Out3(), _state3()
    assert pa.failing_fields(st, out, _errs("completeness")) is None                      # generic area
    assert pa.failing_fields(st, out, _errs("openapiYaml", "intent")) is None             # one unlocalisable issue
    assert pa.failing_fields(st, out, _errs("LLD", "openapi", "dbml", "cdk")) is None     # that is every field


def test_scope_excluded_fields_are_never_reworked():
    st = _state3().model_copy(update={"user_input": (
        "x\n\n## Production scope (confirmed by the reviewer)\n- Produce ONLY these artifacts: LLD, OPENAPI.\n"
        "- Do NOT produce: DBML, CDK.\n")})
    assert pa.failing_fields(st, _Out3(), _errs("openapiYaml", "dbmlSchema")) == ["openapiYaml"]


async def _run_validated(monkeypatch, verdict_issues, settings=None):
    settings = settings or SimpleNamespace(VALIDATION_ENABLED=True, VALIDATION_MAX_REPAIRS=1)
    calls: list[tuple[list[str], bool, str | None]] = []

    async def fake_generate(deps, state, emit, *, rework=None):
        calls.append((list(state.retrigger_fields), state.per_artifact, rework))
        return _Out3()

    verdicts = iter([ValidationVerdict(ok=False, issues=verdict_issues, reworkInstructions="fix it"),
                     ValidationVerdict(ok=True)])

    async def fake_validate(deps, state, emit, out):
        return next(verdicts)

    monkeypatch.setattr(pa, "_generate", fake_generate)
    monkeypatch.setattr(pa, "_validate_output", fake_validate)
    from .conftest import FakeAudit
    deps = SimpleNamespace(settings=settings, audit=FakeAudit(), db=None)
    events: list[dict] = []
    await pa._generate_validated(deps, _state3(), events.append)
    return calls, events


async def test_rework_regenerates_only_the_flagged_artifacts(monkeypatch):
    calls, events = await _run_validated(monkeypatch, _errs("openapiYaml"))
    assert calls[0] == ([], False, None)                               # first run: everything
    assert calls[1] == (["openapiYaml"], True, "fix it")               # rework: one artifact, via the part cache
    assert any("reworking only openapiYaml (1 of 5 artifacts)" in e.get("label", "") for e in events)


async def test_rework_regenerates_everything_when_it_cannot_localise(monkeypatch):
    calls, _ = await _run_validated(monkeypatch, _errs("completeness"))
    assert calls[1] == ([], False, "fix it")


async def test_localised_rework_can_be_switched_off(monkeypatch):
    settings = SimpleNamespace(VALIDATION_ENABLED=True, VALIDATION_MAX_REPAIRS=1, VALIDATION_LOCALISED_REWORK=False)
    calls, _ = await _run_validated(monkeypatch, _errs("openapiYaml"), settings)
    assert calls[1] == ([], False, "fix it")


# ================================================================== planner node makes no model call
async def test_the_planner_node_is_deterministic_and_never_calls_the_model():
    from app.graph import pipeline as pl

    class NoLlm:
        def __getattr__(self, name):
            raise AssertionError("the planner node must not call the LLM")

    emitted: list[dict] = []
    deps = SimpleNamespace(llm=NoLlm())
    st = AgentState(project_id="p", session_id="s", current_phase=3, stage_template=3,
                    stage_name="Technical Design", user_input="Design the order service in detail please")
    out = await pl._planner({"state": st, "phase_result": None},
                            {"configurable": {"deps": deps, "emit": emitted.append}})
    plan = out["state"].plan
    assert [s.id for s in plan][:2] == ["generate", "validate"] and plan[-1].id == "gate"
    assert {"spectral_lint_openapi", "github_commit_lld_artefacts"} <= {s.tool for s in plan}
    assert any(e["type"] == "plan" for e in emitted) and any(e["type"] == "model" for e in emitted)


async def test_status_queries_still_take_the_fast_path():
    from app.graph import pipeline as pl

    st = AgentState(project_id="p", session_id="s", current_phase=1, stage_template=1, user_input="what is the status")
    out = await pl._planner({"state": st, "phase_result": None},
                            {"configurable": {"deps": SimpleNamespace(llm=None), "emit": lambda e: None}})
    assert [s.tool for s in out["state"].plan] == ["status"]


# ================================================================== compression runs in parallel
class _CountingLlm:
    def __init__(self, delay=0.05):
        self.active = self.peak = self.calls = 0
        self.delay = delay

    async def generate(self, **kw):
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(self.delay)
        self.active -= 1
        return SimpleNamespace(content="short summary of the artifact")


def _artifacts(n: int, size: int = 4000) -> list[ContextArtifact]:
    return [ContextArtifact(phase=i + 1, type="DOC", title=f"doc{i}", summary="s", content="x" * size)
            for i in range(n)]


async def test_context_compression_summarises_in_parallel_and_fits_the_budget():
    from app.domain.models import estimate_tokens
    from app.services.context import build_context_block

    llm = _CountingLlm()
    arts = _artifacts(6)                                              # ~6k tokens, budget 2k
    block, compressed = await build_context_block(arts, 2_000, llm)
    assert compressed and estimate_tokens(block) <= 2_000
    assert llm.peak > 1                                               # concurrent, not one at a time
    assert llm.calls <= 6
    assert "short summary of the artifact" in block and "x" * 400 not in block


async def test_compression_makes_no_more_calls_than_needed():
    from app.services.context import build_context_block

    llm = _CountingLlm()
    arts = _artifacts(6)
    # budget leaves room for most bodies: only the oldest few need summarising
    block, _ = await build_context_block(arts, 4_500, llm)
    assert 0 < llm.calls < 6
    assert "xxxx" in block                                           # newer artifacts stay verbatim


async def test_compression_is_a_noop_under_budget():
    from app.services.context import build_context_block

    llm = _CountingLlm()
    block, compressed = await build_context_block(_artifacts(1, 100), 4_000, llm)
    assert not compressed and llm.calls == 0


# ================================================================== the machinery localised rework relies on
async def test_a_retrigger_after_a_full_run_regenerates_only_the_named_part():
    """Localised rework = retrigger_fields + the parts the first run saved. Prove the pair:
    one model call for the flagged field, everything else reused and nothing cleared."""
    from .test_resume import FakeLlm, PartDb, _run

    db, first = PartDb(), FakeLlm()
    await _run(db, first)
    assert sorted(first.calls) == ["alpha", "beta", "gamma"] and len(db.parts) == 3

    db.cleared = False
    second = FakeLlm()
    events = await _run(db, second, retrigger_fields=["beta"])
    assert second.calls == ["beta"] and not db.cleared
    assert {e["part"] for e in events if e["type"] == "part" and e["status"] == "done"} >= {"alpha", "beta", "gamma"}
    assert all(p["status"] == "done" for p in db.parts.values())
