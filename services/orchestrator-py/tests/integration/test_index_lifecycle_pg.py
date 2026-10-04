"""The project-memory loop end to end on a real Postgres: stage on generation, commit on approval."""


import pytest

from app.agile.agents import LlmBuild, run_build
from app.agile.index_service import IndexService
from app.agile.service import AgileLifecycle
from app.agile.specs import DesignDelta, SpecChange, parse_spec
from app.agile.wiring import register_index_hooks
from app.domain.errors import SdlcError
from app.domain.models import AgentState, ContextArtifact
from app.services.content_store import FilesystemContentStore
from app.services.gates import GateService
from app.services.publisher import PublishService

from .helpers import _approve, _finish_project_stages, _stage
from .test_backlog_pg import FakeLlm, deps_for, story

pytestmark = pytest.mark.asyncio


class FakeMcp:
    def __init__(self):
        self.calls = []
        self.fail_on = None

    async def call(self, tool, args):
        self.calls.append((tool, args))
        if self.fail_on == tool:
            raise RuntimeError("connector down")
        if tool == "github_commit_index":
            return {"commitSha": f"c{len([c for c in self.calls if c[0] == tool]):039d}", "parentSha": None,
                    "treeSha": "t" * 40, "branch": args["branch"], "htmlUrl": "http://x", "noop": False}
        if tool == "github_open_pull_request":
            return {"number": 7, "url": "http://pr/7", "created": True}
        return {}


@pytest.fixture
async def idx(env, tmp_path):
    e = env
    e.mcp = FakeMcp()
    e.content = FilesystemContentStore(str(tmp_path))
    e.publisher = PublishService(e.pg, e.content, e.mcp, e.audit)
    e.index = IndexService(e.pg, e.content, e.audit, e.publisher, e.wf, default_branch="main")
    e.proposals.index = e.index
    register_index_hooks(e.agile, e.index, e.proposals)

    async def regen(*_a):
        return None

    e.gates = GateService(e.pg, e.dynamo, e.audit, e.authz, e.wf, regen, e.publisher, e.agile)
    e.lifecycle = AgileLifecycle(e.agile, e.gates, e.index, e.pg)
    return e


async def generated(e, key):
    """What chat does when a stage finishes generating: PENDING_REVIEW, then the lifecycle's hooks."""
    st = await _stage(e, key)
    await e.dynamo.put_phase_state(project_id=e.pid, phase=st["seq"], status="PENDING_REVIEW", reviewer_role=st["reviewerRole"])
    await e.agile.after_generation(e.pid, st["seq"], "po@t.local")
    return st


async def approve(e, key, by=None):
    st = await _stage(e, key)
    return await e.gates.review(project_id=e.pid, phase=st["seq"], decision="APPROVE", comments=None, user=by or e.po)


def commits(e):
    return [a for t, a in e.mcp.calls if t == "github_commit_index"]


async def run_sprint(e, n, *, delta=None, items=2, base_hash=None):
    """Drive sprint n through all five stages with real gates; the Build agent describes `delta`."""
    s = await e.agile.start_sprint(e.pid, e.po, goal=f"Goal {n}", capacity=20)
    sprint = f"S-{n:03d}"
    ks = [await story(e, f"Story {n}.{i}", est=3, status="ready") for i in range(items)]
    for k in ks:
        await e.backlog.add_to_sprint(e.pid, e.po, k["key"])
    await e.backlog.update(e.pid, e.po, ks[0]["key"], {"components": ["orders"]}, expected_version=None)
    await _approve(e, f"refine@{sprint}"); await _approve(e, f"plan@{sprint}")
    out = LlmBuild(summary="done", designDelta=delta or DesignDelta(summary="d", changes=[
        SpecChange(component="orders", section=f"Sprint {n}", op="add", content=f"Behaviour from sprint {n}", rationale="stories")]),
        testDelta="# tests", incrementNotes="# notes")
    st = await _stage(e, f"build@{sprint}")
    state = AgentState(project_id=e.pid, session_id="s", current_phase=st["seq"], stage_template=7, stage_name=st["name"],
                       user_input="build", agile_role="build", iteration_id=st["iterationId"],
                       custom_persona=st["persona"], custom_outputs=list(st["outputs"]))
    await run_build(deps_for(e, FakeLlm(out)), state, lambda _e: None)
    await e.dynamo.put_phase_state(project_id=e.pid, phase=st["seq"], status="PENDING_REVIEW", reviewer_role="TA")
    await e.gates.review(project_id=e.pid, phase=st["seq"], decision="APPROVE", comments=None, user=e.admin)   # build: override
    for k in ks:
        await e.backlog.set_status(e.pid, e.dev, k["key"], "in_progress", expected_version=None)
        await e.backlog.set_status(e.pid, e.po, k["key"], "done", expected_version=None)
    await _approve(e, f"review@{sprint}")
    await generated(e, f"retro@{sprint}")
    await approve(e, f"retro@{sprint}")
    return s


async def test_charter_is_committed_when_the_vision_stage_is_approved(idx):
    e = idx
    await e.agile.enable(e.pid, e.pm, methodology="scrum")
    await generated(e, "vision")
    assert commits(e) == []                                              # staged + queued, NOT committed yet
    st = await _stage(e, "vision")
    assert await e.publisher.has_pending(e.pid, st["seq"])
    await approve(e, "vision", by=e.admin)
    (c,) = commits(e)
    assert c["branch"] == "devmind/index" and c["strategy"] == "index-branch"        # strategy B is the default
    paths = {f["path"] for f in c["files"]}
    assert {".devmind/charter.json", ".devmind/charter.md", ".devmind/manifest.json", ".devmind/README.md"} <= paths
    assert all(p.startswith(".devmind/") for p in paths)
    status = await e.index.status(e.pid)
    assert status["unpublished"] == {"added": 0, "modified": 0, "deleted": 0} and status["publishedCommit"]


async def test_sprint_close_commits_digest_release_and_specs_atomically(idx):
    e = idx
    await e.agile.enable(e.pid, e.pm, methodology="scrum")
    await generated(e, "vision"); await approve(e, "vision", by=e.admin)
    await generated(e, "runway"); await approve(e, "runway", by=e.admin)
    before = len(commits(e))
    await e.agile.start_sprint(e.pid, e.po, goal="x")           # keep a sprint open for run_sprint's own start? no:
    await e.agile.cancel_sprint(e.pid, e.pm, (await e.pg.get_open_iteration(e.pid))["id"])
    await run_sprint(e, 2)                                       # sprint numbering continues after the cancelled one
    new = commits(e)[before:]
    assert len(new) == 1                                          # ONE commit for the whole sprint close
    c = new[0]
    paths = {f["path"] for f in c["files"]}
    assert {".devmind/sprints/S-002/digest.json", ".devmind/releases/R-001/index.json", ".devmind/releases/R-001/specs/orders.md",
            ".devmind/lookup.json", ".devmind/manifest.json"} <= paths
    spec = next(f["content"] for f in c["files"] if f["path"] == ".devmind/releases/R-001/specs/orders.md")
    assert parse_spec(spec)[1]["Sprint 2"] == "Behaviour from sprint 2"
    digest = next(f["content"] for f in c["files"] if f["path"] == ".devmind/sprints/S-002/digest.json")
    assert '"ST-' not in digest and "DM-" in digest
    assert (await e.index.status(e.pid))["unpublished"]["added"] == 0
    assert "index.published" in e.audit.events and "delta.merged" in e.audit.events


async def test_a_stale_delta_is_a_conflict_not_an_overwrite(idx):
    e = idx
    await e.agile.enable(e.pid, e.pm, methodology="scrum")
    await _finish_project_stages(e)
    await run_sprint(e, 1)
    ws = e.index.workspace(e.pid)
    _, sections = parse_spec(await ws.read(".devmind/releases/R-001/specs/orders.md"))
    stale = DesignDelta(summary="d", changes=[SpecChange(component="orders", section="Sprint 1", op="replace",
                                                         content="Overwrite!", rationale="r", baseHash="000000000000")])
    await run_sprint(e, 2, delta=stale)
    _, after = parse_spec(await ws.read(".devmind/releases/R-001/specs/orders.md"))
    assert after["Sprint 1"] == sections["Sprint 1"] != "Overwrite!"          # untouched
    rec = [r for r in e.audit.records if r["event"] == "delta.merged"][-1]
    assert rec["detail"]["conflicts"] and "changed after" in rec["detail"]["conflicts"][0]["reason"]
    notes = await e.pg.pool.fetch("SELECT * FROM notifications WHERE project_id=$1 AND kind='spec_conflict'", e.pid)
    assert len(notes) == 1


async def test_next_sprint_gets_bounded_context_and_the_memory_packet(idx):
    e = idx
    await e.agile.enable(e.pid, e.pm, methodology="scrum")
    await _finish_project_stages(e)
    await run_sprint(e, 1)
    await e.agile.start_sprint(e.pid, e.po, goal="Second", capacity=10)
    wf = await e.wf.view(e.pid)
    s1 = [s["seq"] for s in wf["stages"] if s.get("iterationLabel") == "S-001"]
    vision_seq = (await _stage(e, "vision"))["seq"]
    window = [ContextArtifact(phase=p, type="DOC", title=f"old {p}", summary="s") for p in (vision_seq, *s1)]
    refine2 = await _stage(e, "refine@S-002")
    bounded, extra = await e.lifecycle.context_for(e.pid, refine2, window)
    assert [a.phase for a in bounded] == [vision_seq]                          # sprint-1 artifacts left the window
    assert "Current sprint S-002" in extra and "Project memory" in extra
    assert "S-001" in extra and "orders" in extra                              # history arrives via the packet instead


async def test_release_close_commits_then_opens_the_pr(idx):
    e = idx
    await e.agile.enable(e.pid, e.pm, methodology="scrum")
    await _finish_project_stages(e)
    await run_sprint(e, 1)
    await e.agile.start_release_hardening(e.pid, e.po)
    await generated(e, "release@R-001")
    assert [t for t, _ in e.mcp.calls if t == "github_open_pull_request"] == []          # nothing yet
    await approve(e, "release@R-001", by=e.admin)
    tools = [t for t, _ in e.mcp.calls]
    assert tools[-2:] == ["github_commit_index", "github_open_pull_request"]              # commit FIRST, then the PR
    _, pr = e.mcp.calls[-1]
    assert pr["head"] == "devmind/index" and pr["base"] == "main" and pr["reuseExisting"] is True
    rel = next(f["content"] for f in commits(e)[-1]["files"] if f["path"] == ".devmind/releases/R-001/index.json")
    assert '"closed": true' in rel and "orders" in rel                                     # spec pointer recorded


async def test_default_branch_strategy_commits_to_main_and_opens_no_pr(idx):
    e = idx
    await e.agile.enable(e.pid, e.pm, methodology="scrum", index_strategy="default-branch")
    await _finish_project_stages(e)
    await run_sprint(e, 1)
    await e.agile.start_release_hardening(e.pid, e.po)
    await generated(e, "release@R-001"); await approve(e, "release@R-001", by=e.admin)
    assert all(c["branch"] == "main" for c in commits(e))
    assert not [t for t, _ in e.mcp.calls if t == "github_open_pull_request"]


async def test_failed_publish_keeps_the_gate_pending_and_retry_succeeds_once(idx):
    e = idx
    await e.agile.enable(e.pid, e.pm, methodology="scrum")
    await generated(e, "vision")
    e.mcp.fail_on = "github_commit_index"
    with pytest.raises(SdlcError):
        await approve(e, "vision", by=e.admin)
    st = await _stage(e, "vision")
    assert (await e.dynamo.get_phase_state(e.pid, st["seq"]))["status"] == "PENDING_REVIEW"
    assert (await e.index.status(e.pid))["publishedCommit"] is None
    e.mcp.fail_on = None
    await approve(e, "vision", by=e.admin)
    assert len(commits(e)) == 2 and (await e.index.status(e.pid))["unpublished"]["added"] == 0   # failed try + success
    assert not await e.publisher.has_pending(e.pid, st["seq"])


async def test_regenerating_a_stage_does_not_queue_the_commit_twice(idx):
    e = idx
    await e.agile.enable(e.pid, e.pm, methodology="scrum")
    await generated(e, "vision"); await generated(e, "vision"); await generated(e, "vision")
    st = await _stage(e, "vision")
    queue = await e.publisher._load(e.pid, st["seq"])
    assert [a["tool"] for a in queue] == ["github_commit_index"]


async def test_workspace_integrity_after_a_full_cycle(idx):
    e = idx
    await e.agile.enable(e.pid, e.pm, methodology="scrum")
    await _finish_project_stages(e)
    await run_sprint(e, 1)
    await run_sprint(e, 2)
    from app.devmind_index.integrity import check_integrity
    assert await check_integrity(e.index.workspace(e.pid)) == []
    pk = await e.index.packet(e.pid, components=("orders",))
    assert pk and "orders" in pk.text and pk.tokens <= 8000


async def test_release_stage_keeps_only_the_last_sprints_artifacts(idx):
    e = idx
    await e.agile.enable(e.pid, e.pm, methodology="scrum")
    await _finish_project_stages(e)
    await run_sprint(e, 1)
    await run_sprint(e, 2)
    await e.agile.start_release_hardening(e.pid, e.po)
    wf = await e.wf.view(e.pid)
    s1 = [s["seq"] for s in wf["stages"] if s.get("iterationLabel") == "S-001"]
    s2 = [s["seq"] for s in wf["stages"] if s.get("iterationLabel") == "S-002"]
    window = [ContextArtifact(phase=p, type="DOC", title=f"t{p}", summary="s") for p in (*s1, *s2)]
    bounded, _ = await e.lifecycle.context_for(e.pid, await _stage(e, "release@R-001"), window)
    assert {a.phase for a in bounded} == set(s2)                  # RETRO_NOTES of S-002 stay; S-001 is in the index
