"""Forking a release: carry set, fork record, release-scoped specs and context, the start-release questionnaire."""

import json

import pytest

from app.agile.agents import LlmBuild, run_build
from app.agile.carry import CarryService
from app.agile.release_setup import ReleaseSetupService
from app.agile.specs import DesignDelta, SpecChange, parse_spec, section_hash
from app.domain.errors import SdlcError
from app.domain.models import AgentState

from .helpers import _approve, _finish_project_stages, _stage
from .test_backlog_pg import FakeLlm, deps_for, story
from .test_index_lifecycle_pg import approve, generated, idx  # noqa: F401  (fixture)

pytestmark = pytest.mark.asyncio


async def _run(e, release_id, label, delta, *, items=1):
    """One full sprint of `release_id` through the real gates; the Build agent describes `delta`."""
    s = await e.agile.start_sprint(e.pid, e.po, goal=f"Goal {label}", capacity=20, release_id=release_id)
    ks = [await story(e, f"{label} story {i}", est=3, status="ready") for i in range(items)]
    for k in ks:
        await e.backlog.add_to_sprint(e.pid, e.po, k["key"], iteration_id=s["id"])
    await e.backlog.update(e.pid, e.po, ks[0]["key"], {"components": ["orders"]}, expected_version=None)
    await _approve(e, f"refine@{label}"); await _approve(e, f"plan@{label}")
    out = LlmBuild(summary="done", designDelta=delta, testDelta="# t", incrementNotes="# n")
    st = await _stage(e, f"build@{label}")
    state = AgentState(project_id=e.pid, session_id="s", current_phase=st["seq"], stage_template=7, stage_name=st["name"],
                       user_input="build", agile_role="build", iteration_id=st["iterationId"],
                       custom_persona=st["persona"], custom_outputs=list(st["outputs"]))
    await run_build(deps_for(e, FakeLlm(out)), state, lambda _e: None)
    await e.dynamo.put_phase_state(project_id=e.pid, phase=st["seq"], status="PENDING_REVIEW", reviewer_role="TA")
    await e.gates.review(project_id=e.pid, phase=st["seq"], decision="APPROVE", comments=None, user=e.admin)
    for k in ks:
        await e.backlog.set_status(e.pid, e.dev, k["key"], "in_progress", expected_version=None)
        await e.backlog.set_status(e.pid, e.po, k["key"], "done", expected_version=None)
    await _approve(e, f"review@{label}")
    await generated(e, f"retro@{label}")
    await approve(e, f"retro@{label}")
    return s


def add(section, content, comp="orders"):
    return SpecChange(component=comp, section=section, op="add", content=content, rationale="r")


@pytest.fixture
async def fk(idx):                                   # noqa: F811
    e = idx
    e.carry = CarryService(e.pg, e.index, e.audit)
    e.setup = ReleaseSetupService(e.pg, e.audit, e.agile, e.backlog, e.carry, e.index)
    await e.agile.enable(e.pid, e.pm, methodology="scrum")
    await _finish_project_stages(e)
    e.r1 = (await e.pg.list_releases(e.pid))[0]
    await _run(e, e.r1["id"], "S-001", DesignDelta(summary="d", changes=[
        add("Endpoints", "GET /orders and POST /orders create an order"),
        add("Payments", "Card payments go through the PSP gateway", comp="billing"),
        add("Events", "OrderCreated is published to the bus")]))
    return e


async def spec_sections(e, release, comp="orders"):
    ws = e.index.workspace(e.pid)
    m = await ws.manifest()
    path = f".devmind/releases/{release}/specs/{comp}.md"
    return parse_spec(await ws.read(path))[1] if path in m.files else None


async def test_specs_belong_to_their_release(fk):
    e = fk
    assert set(await spec_sections(e, "R-001")) == {"Endpoints", "Events"}
    assert set(await spec_sections(e, "R-001", "billing")) == {"Payments"}
    m = await e.index.workspace(e.pid).manifest()
    assert {p for p in m.files if "/specs/" in p} == {".devmind/releases/R-001/specs/orders.md",
                                                       ".devmind/releases/R-001/specs/billing.md"}
    assert all(m.files[p].release == "R-001" for p in m.files if "/specs/" in p)


async def test_candidates_cover_specs_requirements_and_decisions(fk):
    e = fk
    got = {c.id: c for c in await e.carry.candidates(e.pid, e.r1)}
    assert {"spec:orders/Endpoints", "spec:orders/Events", "spec:billing/Payments"} <= set(got)
    assert any(k.startswith("req:") and v.kind == "requirement" for k, v in got.items())       # delivered stories
    assert any(v.kind == "decision" for v in got.values())
    assert got["spec:orders/Endpoints"].carriedFrom.hash == section_hash("GET /orders and POST /orders create an order")


async def test_suggestions_are_ranked_by_rules_and_a_model_can_only_pick_real_ids(fk):
    e = fk
    s = await e.carry.suggest(e.pid, e.r1, "extend the orders endpoints with cancellation", components=["orders"])
    assert s["source"] == "rules" and s["suggestions"][0]["id"].startswith("spec:orders/")
    assert "spec:billing/Payments" not in [x["id"] for x in s["suggestions"]]
    assert (await e.carry.suggest(e.pid, e.r1, "zzzz qqqq"))["suggestions"] == []              # nothing relevant

    class Pick:
        def __init__(self, ids):
            self.ids = ids

        async def generate_json(self, **kw):
            from app.agile.carry import LlmCarry, LlmCarryPick
            return LlmCarry(picks=[LlmCarryPick(id=i, reason="needed") for i in self.ids]), None

    e.carry._llm = Pick(["spec:billing/Payments", "spec:invented/Nope", "spec:orders/Events"])
    s = await e.carry.suggest(e.pid, e.r1, "payments and events")
    assert s["source"] == "ai" and [x["id"] for x in s["suggestions"]] == ["spec:billing/Payments", "spec:orders/Events"]

    class Down:
        async def generate_json(self, **kw):
            raise RuntimeError("model unavailable")
    e.carry._llm = Down()
    s = await e.carry.suggest(e.pid, e.r1, "extend the orders endpoints")
    assert s["source"] == "rules" and s["suggestions"]                                          # falls back, never fails


async def test_preview_validates_and_changes_nothing(fk):
    e = fk
    gen_before = (await e.index.workspace(e.pid).manifest()).generation
    n_before = len(await e.pg.list_releases(e.pid))
    p = await e.setup.preview(e.pid, e.po, {"name": "Orders v2", "startFrom": "fork", "sourceRelease": e.r1["id"],
                                            "carry": ["spec:orders/Endpoints"], "unfinishedItems": "none"})
    assert p["valid"] and any("Carry 1 context entry" in s for s in p["steps"]) and p["summary"]["carry"]["entries"] == 1
    bad = await e.setup.preview(e.pid, e.po, {"name": "", "startFrom": "fork"})
    assert not bad["valid"] and any(x.startswith("name") for x in bad["errors"])
    unknown = await e.setup.preview(e.pid, e.po, {"name": "x", "startFrom": "fork", "sourceRelease": e.r1["id"],
                                                  "carry": ["spec:nothing/Here"]})
    assert any("not a candidate" in w for w in unknown["warnings"])
    assert (await e.index.workspace(e.pid).manifest()).generation == gen_before
    assert len(await e.pg.list_releases(e.pid)) == n_before


async def test_forking_starts_an_empty_plate_with_only_the_carried_context(fk):
    e = fk
    res = await e.setup.start(e.pid, e.po, {
        "name": "Orders v2", "goal": "Cancellation and refunds", "startFrom": "fork", "sourceRelease": e.r1["id"],
        "carry": ["spec:orders/Endpoints", "spec:billing/Payments"], "intakeRule": "pool"})
    rel = await e.pg.get_release(res["release"]["id"])
    assert rel["code"] == "R-002" and rel["forked_from"] == e.r1["id"] and rel["fork_baseline"]["sprint"] == "S-001"
    assert (rel["setup"]["status"], rel["setup"]["progress"]["carry"]) == ("complete", {"carried": 2})
    # the new release has ONLY the chosen sections; the source is untouched
    assert set(await spec_sections(e, "R-002")) == {"Endpoints"}
    assert set(await spec_sections(e, "R-002", "billing")) == {"Payments"}
    assert set(await spec_sections(e, "R-001")) == {"Endpoints", "Events"}
    ws = e.index.workspace(e.pid)
    fork = json.loads(await ws.read(".devmind/releases/R-002/fork.json"))
    assert fork["forkedFrom"] == "R-001" and {c["id"] for c in fork["carried"]} == {"spec:orders/Endpoints", "spec:billing/Payments"}
    assert all(c["state"] == "carried" and c["carriedFrom"]["release"] == "R-001" for c in fork["carried"])
    md = await ws.read(".devmind/releases/R-002/carried.md")
    assert "Carried as is" in md and "orders / Endpoints" in md and "Events" not in md
    idx_json = json.loads(await ws.read(".devmind/releases/R-002/index.json"))
    assert idx_json["forkedFrom"] == "R-001" and "orders" in idx_json["specPointers"]
    again = await e.carry.apply(e.pid, rel, e.r1, ["spec:orders/Endpoints", "spec:orders/Events"], actor="po")   # extend later
    assert again["carried"] == ["spec:orders/Events"] and again["rejected"][0]["reason"] == "already carried"


async def test_an_invalid_carry_set_creates_nothing(fk):
    e = fk
    n = len(await e.pg.list_releases(e.pid))
    with pytest.raises(SdlcError) as err:
        await e.setup.start(e.pid, e.po, {"name": "x", "startFrom": "fork", "sourceRelease": e.r1["id"], "carry": ["spec:nope/x"]})
    assert err.value.code == "VALIDATION_FAILED" and len(await e.pg.list_releases(e.pid)) == n
    with pytest.raises(SdlcError) as err:                                    # nothing stable to fork from
        await e.agile.create_release(e.pid, e.po, name="parallel")
        r2 = (await e.pg.list_releases(e.pid))[-1]
        await e.setup.start(e.pid, e.po, {"name": "x", "startFrom": "fork", "sourceRelease": r2["id"]})
    assert "closed sprint" in err.value.message


async def test_carried_sections_become_modified_new_or_retired_and_the_source_never_changes(fk):
    e = fk
    res = await e.setup.start(e.pid, e.po, {"name": "v2", "startFrom": "fork", "sourceRelease": e.r1["id"],
                                            "carry": ["spec:orders/Endpoints", "spec:orders/Events"]})
    r2 = res["release"]
    carried_hash = section_hash("GET /orders and POST /orders create an order")
    delta = DesignDelta(summary="d", changes=[
        SpecChange(component="orders", section="Endpoints", op="replace", content="GET/POST/DELETE /orders",
                   rationale="cancel", baseHash=carried_hash),
        SpecChange(component="orders", section="Events", op="remove", rationale="dropped",
                   baseHash=section_hash("OrderCreated is published to the bus")),
        add("Cancellation", "A paid order can be cancelled within 24h")])
    await _run(e, r2["id"], "S-002", delta)
    rel = await e.pg.get_release(r2["id"])
    view = await e.carry.view(e.pid, rel)
    states = {c["id"]: c["state"] for c in view["carried"]}
    assert states == {"spec:orders/Endpoints": "modified", "spec:orders/Events": "retired"}
    assert [n["id"] for n in view["new"]] == ["spec:orders/Cancellation"]
    assert view["counts"] == {"modified": 1, "retired": 1, "new": 1}
    fork = json.loads(await e.index.workspace(e.pid).read(".devmind/releases/R-002/fork.json"))
    assert {c["id"]: c["state"] for c in fork["carried"]} == states          # the FILE says the same (refreshed on delta)
    assert set(await spec_sections(e, "R-001")) == {"Endpoints", "Events"}   # the source release is untouched
    # the source evolves later: an advisory, never an automatic update
    await e.index.apply_delta(e.pid, DesignDelta(changes=[SpecChange(
        component="orders", section="Events", op="replace", content="OrderCreated and OrderPaid",
        baseHash=section_hash("OrderCreated is published to the bus"))]), "R-001")
    assert "spec:orders/Events" in (await e.carry.view(e.pid, rel))["sourceChanged"]


async def test_context_packet_is_scoped_to_the_release_and_includes_what_it_carried(fk):
    e = fk
    res = await e.setup.start(e.pid, e.po, {"name": "v2", "startFrom": "fork", "sourceRelease": e.r1["id"],
                                            "carry": ["spec:orders/Endpoints"]})
    await _run(e, res["release"]["id"], "S-002", DesignDelta(changes=[add("Cancellation", "Cancel within 24h")]))
    p2 = await e.index.packet(e.pid, components=("orders",), release="R-002")
    assert "CARRIED CONTEXT" in p2.text and "orders / Endpoints" in p2.text
    digests = p2.text.split("## SPRINT DIGESTS")[1].split("## CARRIED CONTEXT")[0] if "## SPRINT DIGESTS" in p2.text else ""
    assert "S-001" not in digests                                               # R-001's sprint history stays in R-001
    p1 = await e.index.packet(e.pid, components=("orders",), release="R-001")
    assert "S-001" in p1.text and "CARRIED CONTEXT" not in p1.text
    lk = json.loads(await e.index.workspace(e.pid).read(".devmind/lookup.json"))
    assert lk["releases"]["R-002"]["name"] == "v2"


async def test_an_interrupted_start_resumes_without_repeating_steps(fk):
    e = fk
    a = await story(e, "left over", est=2, status="ready")
    await e.backlog.claim_into_release(e.pid, e.po, e.r1["id"], [a["key"]])
    answers = {"name": "v2", "startFrom": "fork", "sourceRelease": e.r1["id"], "carry": ["spec:orders/Endpoints"],
               "unfinishedItems": "all", "jiraLabel": "orders-v2"}
    real = e.backlog.move_unfinished
    calls = {"n": 0}

    async def flaky(*args, **kw):
        calls["n"] += 1
        raise RuntimeError("database hiccup")
    e.backlog.move_unfinished = flaky
    with pytest.raises(SdlcError) as err:
        await e.setup.start(e.pid, e.po, answers)
    assert err.value.details["resumable"] and err.value.details["progress"].get("carry")
    e.backlog.move_unfinished = real
    rel = (await e.pg.list_releases(e.pid))[-1]
    assert err.value.details["releaseId"] == rel["id"]
    assert rel["setup"]["progress"].get("carry") and not rel["setup"]["progress"].get("moved")
    assert "release.setup_incomplete" in e.audit.events
    done = await e.setup.start(e.pid, e.po, answers, resume_release_id=rel["id"])
    assert done["progress"]["moved"] == {"moved": 1}
    assert len([r for r in await e.pg.list_releases(e.pid) if r["name"] == "v2"]) == 1
    fork = json.loads(await e.index.workspace(e.pid).read(".devmind/releases/R-002/fork.json"))
    assert len(fork["carried"]) == 1                                            # the carry was not repeated
    row = await e.pg.get_backlog_item(e.pid, a["key"])
    assert row["release_id"] == rel["id"] and "orders-v2" in row["labels"]


async def test_epic_intake_and_first_sprint_through_the_questionnaire(fk):
    e = fk
    epic = await e.backlog.create(e.pid, e.po, {"title": "Refunds", "type": "epic"})
    child = await e.backlog.create(e.pid, e.po, {"title": "Refund a card", "epicKey": epic["key"]})
    out = await e.setup.start(e.pid, e.po, {"name": "Refunds", "intakeRule": "epic", "epics": [epic["key"]],
                                            "adoptExistingEpicItems": True, "firstSprint": "now", "usePool": False})
    rid = out["release"]["id"]
    assert (await e.pg.get_backlog_item(e.pid, child["key"]))["release_id"] == rid
    assert out["release"]["intakeRule"] == "epic" and out["release"]["usePool"] is False
    assert out["release"]["openIterationId"] and out["progress"]["sprint"] is True


async def test_authority_defaults_and_locks(fk):
    e = fk
    for fn in (e.setup.questions, ):
        with pytest.raises(SdlcError) as err:
            await fn(e.pid, e.dev)
        assert err.value.code == "FORBIDDEN"
    with pytest.raises(SdlcError):
        await e.setup.preview(e.pid, e.dev, {"name": "x"})
    with pytest.raises(SdlcError) as err:                                       # a locked question needs a default
        await e.agile.update_settings(e.pid, e.pm, {"release_locks": ["usePool"]})
    assert "needs a default" in err.value.message
    with pytest.raises(SdlcError):                                              # junk defaults are refused
        await e.agile.update_settings(e.pid, e.pm, {"release_defaults": {"intakeRule": "fix-version"}})
    await e.agile.update_settings(e.pid, e.pm, {"release_defaults": {"usePool": False, "intakeRule": "pool"},
                                                "release_locks": ["usePool"]})
    q = await e.setup.questions(e.pid, e.po)
    assert q["locked"] == ["usePool"] and q["defaults"]["usePool"] is False
    out = await e.setup.start(e.pid, e.po, {"name": "locked", "usePool": True})   # the person's answer is overridden
    assert out["release"]["usePool"] is False
