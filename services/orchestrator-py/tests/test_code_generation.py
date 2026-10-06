"""Two-step code generation: propose a structure, approve it at the gate, implement exactly that structure,
commit only after the code is approved - every step recorded."""
from __future__ import annotations

import io
import json
import os
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agents import code_generation as cg
from app.agents import phase_agents as pa
from app.agents.schemas import CodeBatchOutput, CodeStructureOutput, FileEntry
from app.domain.errors import SdlcError
from app.domain.models import AgentState
from app.services import code_structure as cs
from app.services.code_gen import CodeGenService

from .conftest import FakeAudit, make_user

PROPOSAL = CodeStructureOutput(
    summary="Layered service", conventions=["snake_case modules", "tests mirror src"],
    directories=[{"path": "src", "purpose": "source"}],
    files=[{"path": "src/app/main.py", "purpose": "entry point", "kind": "source", "layer": "api"},
           {"path": "src/app/service.py", "purpose": "core rules", "kind": "source", "layer": "domain"},
           {"path": "tests/test_service.py", "purpose": "tests for the rules", "kind": "test"},
           {"path": "README.md", "purpose": "how to run", "kind": "docs"}],
    branch="feature/quicksilver", commitMessage="feat: quicksilver", prTitle="Implement quicksilver", prBody="body", checklist=["tests"])


class Db:
    """Just enough of the database for the two steps."""

    def __init__(self):
        self.plans: list[dict] = []
        self.artefacts: list[dict] = []

    async def latest_code_plan(self, pid, phase):
        live = [p for p in self.plans if p["status"] != "superseded"]
        return max(live, key=lambda p: p["version"]) if live else None

    async def insert_code_plan(self, *, project_id, phase, structure, meta, artefact_id, proposed_by):
        for p in self.plans:
            p["status"] = "superseded"
        row = {"id": f"cp{len(self.plans) + 1}", "version": len(self.plans) + 1, "status": "proposed", "structure": structure, "meta": meta,
               "artefact_id": artefact_id, "decided_by": None, "decided_at": None, "implemented_at": None, "committed_at": None, "commit_ref": None}
        self.plans.append(row)
        return row

    async def approve_code_plan(self, plan_id, *, approver, comments=None):
        p = next(p for p in self.plans if p["id"] == plan_id)
        if p["status"] != "proposed":
            return None
        p.update(status="approved", decided_by=approver, decided_at=datetime.now(UTC))
        return p

    async def set_code_plan_artefact(self, plan_id, artefact_id):
        next(p for p in self.plans if p["id"] == plan_id)["artefact_id"] = artefact_id

    async def mark_code_plan_implemented(self, plan_id):
        next(p for p in self.plans if p["id"] == plan_id)["implemented_at"] = datetime.now(UTC)

    async def mark_code_plan_committed(self, plan_id, ref):
        p = next(p for p in self.plans if p["id"] == plan_id)
        p.update(committed_at=datetime.now(UTC), commit_ref=ref)

    async def supersede_code_plan(self, plan_id):
        next(p for p in self.plans if p["id"] == plan_id)["status"] = "superseded"

    async def latest_artefact_version(self, *a):
        return None

    async def insert_artefact(self, **kw):
        self.artefacts.append({**kw, "id": kw["artefact_id"], "is_latest": True, "phase": kw["phase"], "type": kw["type_"]})

    async def latest_artefact_row(self, pid, phase, type_, title):
        return next((a for a in reversed(self.artefacts) if a["type"] == type_ and a["title"] == title), None)

    async def list_phase_artefacts(self, pid, phase):
        return [a for a in self.artefacts if a["phase"] == phase]

    async def get_project(self, pid):
        return {"id": pid, "name": "Quicksilver EIP"}

    async def clear_phase_signoffs(self, pid, phase):
        self.cleared = True


class Llm:
    def __init__(self, drop=(), extra=()):
        self.calls: list[str] = []
        self.drop, self.extra = set(drop), list(extra)

    async def generate_json(self, *, tag, messages, schema, **kw):
        self.calls.append(tag)
        res = SimpleNamespace(provider="bedrock", model="m", content="{}", usage={"promptTokens": 5, "completionTokens": 7}, attempts=1)
        if schema is CodeStructureOutput:
            return PROPOSAL, res
        import re
        paths = re.search(r"<!--\s*files:\s*(.*?)\s*-->", messages[1]["content"]).group(1).split(" | ")
        files = [FileEntry(path=p, content=f"# {p}\nprint('x')\n") for p in paths if p not in self.drop] + [FileEntry(path=e, content="evil") for e in self.extra]
        return CodeBatchOutput(files=files or [FileEntry(path="x", content="y")]), res

    async def generate(self, **kw):
        return SimpleNamespace(content="x", provider="bedrock")


class Mcp:
    """The verification tools (Sonar, ZAP ...) answer with a passing report."""

    async def call(self, name, args, **kw):
        if name == "sonarqube_analyse":
            return {"reportMarkdown": "# Sonar", "qualityGate": "PASSED", "bugs": 0, "codeSmells": 1, "coveragePct": 91}
        if name == "zap_baseline_scan":
            return {"reportMarkdown": "# ZAP", "result": "PASSED"}
        return {"reportMarkdown": "# ok", "passed": 1, "total": 1}


class Content:
    mode = "fs"

    def __init__(self):
        self.store: dict[str, str] = {}

    async def put(self, k, c):
        self.store[k] = c

    async def get(self, k):
        return self.store.get(k)


def _deps(llm=None, db=None, **settings):
    s = SimpleNamespace(PUBLISH_ON_APPROVAL=True, CONTEXT_TOKEN_THRESHOLD=16_000, PHASE_MAX_TOKENS=4000, QUALITY_GATE_ENABLED=True,
                        COVERAGE_MIN_PERCENT=80, LINT_REQUIRED=True, CODE_TWO_STEP_ENABLED=True, **settings)
    rag = SimpleNamespace(index_artifact=lambda *a: _noop())
    return SimpleNamespace(llm=llm or Llm(), db=db or Db(), audit=FakeAudit(), content=Content(), rag=rag, canon=None, settings=s,
                           mcp=Mcp(), telemetry=None, monitor=None, formworks=None)


async def _noop():
    return None


def _state(**kw):
    base = dict(project_id="p1", session_id="s", current_phase=6, stage_template=6, stage_name="Implementation & Delivery",
                user_input="Build the integration", tech_stack="Python 3.12 + FastAPI")
    base.update(kw)
    return AgentState(**base)


def _events(deps):
    return deps.audit.events


# ---------------------------------------------------------------- step 1
async def test_step_one_proposes_a_structure_writes_no_code_and_waits_for_approval():
    deps, events = _deps(), []
    res = await cg.run(deps, _state(), events.append)
    assert res.gate_status == "PENDING_REVIEW" and [a.type for a in res.new_artifacts] == ["CODE_STRUCTURE"]
    plan = deps.db.plans[0]
    assert plan["status"] == "proposed" and plan["version"] == 1 and plan["meta"]["branch"] == "feature/quicksilver"
    paths = [f["path"] for f in plan["structure"]["files"]]
    assert "src/app/main.py" in paths and any(p.endswith(("pyproject.toml", ".coveragerc", "ruff.toml")) or "cov" in p.lower() or "lint" in p.lower() or p.endswith(".toml") for p in paths)
    assert deps.llm.calls == ["stage6_code_structure"]                                  # one planning call, no code calls
    assert not [a for a in deps.db.artefacts if a["type"] in ("APP_CODE", "UNIT_TESTS")]
    saved = next(a for a in deps.db.artefacts if a["type"] == "CODE_STRUCTURE")
    assert "## Directory structure" in saved["content"] and "src/app/main.py" in saved["content"] and plan["artefact_id"] == saved["id"]
    ev = _events(deps)
    assert "ai.generation" in ev and "code.structure.proposed" in ev
    assert "waiting for approval" in " ".join(e.get("label", "") for e in events)


async def test_a_rejected_proposal_is_revised_with_the_reviewers_feedback_as_a_new_version():
    deps = _deps()
    await cg.run(deps, _state(), lambda e: None)
    seen = {}

    async def spy(*, tag, messages, schema, **kw):
        seen["user"] = messages[1]["content"]
        return PROPOSAL, SimpleNamespace(provider="p", model="m", content="{}", usage={"promptTokens": 1, "completionTokens": 1}, attempts=1)

    deps.llm.generate_json = spy
    await cg.run(deps, _state(amend_comments="Split the service into two modules"), lambda e: None)
    assert "NOT approved" in seen["user"] and "Split the service" in seen["user"] and "src/app/service.py" in seen["user"]
    assert [p["status"] for p in deps.db.plans] == ["superseded", "proposed"] and deps.db.plans[1]["version"] == 2


# ---------------------------------------------------------------- step 2
async def _approved(deps):
    await cg.run(deps, _state(), lambda e: None)
    deps.db.plans[-1]["status"] = "approved"


async def test_step_two_writes_exactly_the_approved_files_and_queues_the_commit_for_the_code_approval():
    deps = _deps()
    await _approved(deps)
    sink_token = pa._publish_sink.set([])
    try:
        res = await cg.run(deps, _state(), lambda e: None)
        queued = pa._publish_sink.get()
    finally:
        pa._publish_sink.reset(sink_token)
    approved_paths = {f["path"] for f in deps.db.plans[-1]["structure"]["files"]}
    code = {a["title"]: a for a in deps.db.artefacts if a["type"] in ("APP_CODE", "UNIT_TESTS")}
    assert set(code) == approved_paths                                                  # every agreed file, nothing else
    assert code["tests/test_service.py"]["type"] == "UNIT_TESTS" and code["src/app/main.py"]["type"] == "APP_CODE"
    assert [q["tool"] for q in queued] == ["github_create_branch", "github_commit_code", "github_create_pull_request"]
    commit = queued[1]["args"]
    assert commit["branch"] == "feature/quicksilver" and {f["path"] for f in commit["files"]} == approved_paths
    assert res.gate_status == "PENDING_REVIEW" and "committed to GitHub" in res.summary
    assert deps.db.plans[-1]["implemented_at"] is not None
    ev = _events(deps)
    assert "code.generation.started" in ev and "code.generation.completed" in ev and ev.count("ai.generation") >= 2
    assert not any("committed" == e for e in ev)                                       # nothing is committed during generation


async def test_files_outside_the_agreed_structure_are_never_accepted_and_missing_ones_are_retried():
    deps = _deps(llm=Llm(extra=["src/app/evil.py"]))
    await _approved(deps)
    token = pa._publish_sink.set([])
    try:
        await cg.run(deps, _state(), lambda e: None)
    finally:
        pa._publish_sink.reset(token)
    assert "src/app/evil.py" not in {a["title"] for a in deps.db.artefacts}
    assert "code.generation.rejected_files" in _events(deps)

    stubborn = _deps(llm=Llm(drop=["src/app/service.py"]))
    await _approved(stubborn)
    token = pa._publish_sink.set([])
    try:
        with pytest.raises(SdlcError, match="did not write 1 agreed file"):
            await cg.run(stubborn, _state(), lambda e: None)
    finally:
        pa._publish_sink.reset(token)
    assert not [a for a in stubborn.db.artefacts if a["type"] in ("APP_CODE", "UNIT_TESTS")]     # nothing half-saved
    assert stubborn.db.plans[-1]["status"] == "approved"                                         # retry resumes at step 2, not step 1


async def test_the_platforms_own_quality_files_are_written_without_the_model():
    deps = _deps()
    await _approved(deps)
    platform = [f["path"] for f in deps.db.plans[-1]["structure"]["files"] if f["layer"] == "quality"]
    assert platform
    token = pa._publish_sink.set([])
    try:
        await cg.run(deps, _state(), lambda e: None)
    finally:
        pa._publish_sink.reset(token)
    asked = {p for c in deps.llm.calls for p in platform if c.endswith("batch")}          # none of them went through a model batch
    assert not asked
    body = next(a for a in deps.db.artefacts if a["title"] == platform[0])["content"]
    assert "80" in body or "cov" in body.lower() or body.strip()


async def test_the_old_single_step_behaviour_is_still_available_behind_the_switch(monkeypatch):
    called = {}

    async def legacy(deps, state, emit):
        called["legacy"] = True
        raise RuntimeError("stop here")

    monkeypatch.setattr(pa, "_generate_validated", legacy)
    off = _deps()
    off.settings.CODE_TWO_STEP_ENABLED = False
    with pytest.raises(RuntimeError):
        await pa._run_phase6(off, _state(), lambda e: None)
    assert called == {"legacy": True} and not off.db.plans


# ---------------------------------------------------------------- the gate
class Dyn:
    def __init__(self):
        self.status = {}

    async def transition_phase_state(self, *, project_id, phase, expected, next_status, reviewed_by=None, comments=None):
        assert self.status.get(phase, "PENDING_REVIEW") == expected
        self.status[phase] = next_status


def _service(db, jobs, enabled=True):
    async def enqueue(pid, phase, actor):
        jobs.append((pid, phase, actor))
        return {"jobId": "j1"}

    async def can_write(pid, phase, user):
        return user.role != "QA"

    audit = FakeAudit()
    wf = SimpleNamespace(view=lambda pid: _wf())
    authz = SimpleNamespace(assert_project_access=lambda pid, user: _noop())
    svc = CodeGenService(db, Dyn(), audit, authz, Content(), wf, enqueue, can_write, enabled=enabled)
    return svc, audit


async def _wf():
    return {"stages": [{"seq": 6, "key": "dev", "name": "Implementation & Delivery", "template": 6}, {"seq": 1, "key": "po", "name": "Req", "template": 1}]}


STAGE = {"seq": 6, "key": "dev", "name": "Implementation & Delivery", "template": 6}


async def test_approving_the_structure_starts_implementation_instead_of_completing_the_stage():
    db, jobs = Db(), []
    await cg.run(_deps(db=db), _state(), lambda e: None)
    svc, audit = _service(db, jobs)
    plan = await svc.structure_pending("p1", 6, STAGE)
    assert plan and await svc.structure_pending("p1", 1, {"template": 1}) is None          # only the implementation stage
    out = await svc.approve_structure(project_id="p1", phase=6, stage=STAGE, plan=plan, user=make_user("PROJECT_MANAGER"), override=False)
    assert out["status"] == "IN_PROGRESS" and out["structureApproved"] and jobs == [("p1", 6, "project_manager@sdlc.local")]
    assert db.plans[-1]["status"] == "approved" and db.plans[-1]["decided_by"] == "project_manager@sdlc.local"
    ev = audit.events
    assert ev == ["code.structure.approved", "code.generation.queued"]
    assert await svc.structure_pending("p1", 6, STAGE) is None                              # the NEXT approval is the code's
    with pytest.raises(SdlcError, match="already decided"):
        await svc.approve_structure(project_id="p1", phase=6, stage=STAGE, plan=plan, user=make_user("PROJECT_MANAGER"), override=False)


async def test_gate_routes_the_structure_approval_and_only_publishes_after_the_code_approval():
    from app.services.gates import GateService

    db, jobs = Db(), []
    await cg.run(_deps(db=db), _state(), lambda e: None)
    svc, _ = _service(db, jobs)
    published: list = []

    class Pub:
        async def publish(self, **kw):
            published.append(kw)
            return {"published": 3, "results": [{"tool": "github_commit_code", "ref": "abc123", "url": "https://gh/c/abc123"},
                                                {"tool": "github_create_pull_request", "ref": 7, "url": "https://gh/pr/7", "prNumber": 7}]}

    class GDyn(Dyn):
        async def clear_phase_stale(self, **kw):
            pass

    g = GateService.__new__(GateService)
    g._db, g._dynamo, g._audit, g._publisher, g._code = db, svc._dynamo, FakeAudit(), Pub(), svc

    async def adv(*a, **k):
        return 7

    g._advance_if_level_done = adv
    user = make_user("PROJECT_MANAGER")
    first = await g._finalize_gate("p1", 6, STAGE, {}, user, True, {})
    assert first["structureApproved"] and published == []                                   # nothing committed at structure approval
    svc._dynamo.status[6] = "PENDING_REVIEW"                                                # step 2 finished, code in review
    svc._dynamo.clear_phase_stale = GDyn().clear_phase_stale
    db.plans[-1]["implemented_at"] = datetime.now(UTC)
    db.pool = SimpleNamespace(execute=lambda *a: _noop())
    second = await g._finalize_gate("p1", 6, STAGE, {}, user, True, {})
    assert len(published) == 1 and second["status"] == "APPROVED"
    assert db.plans[-1]["committed_at"] is not None and db.plans[-1]["commit_ref"] == "abc123"


# ---------------------------------------------------------------- explorer view, reset and zip
async def _with_code(enabled=True):
    deps, jobs = _deps(), []
    await _approved(deps)
    token = pa._publish_sink.set([])
    try:
        await cg.run(deps, _state(), lambda e: None)
    finally:
        pa._publish_sink.reset(token)
    svc, audit = _service(deps.db, jobs, enabled)
    svc._content = deps.content
    return deps, svc, audit


async def test_the_view_gives_a_tree_with_per_file_state_and_the_zip_holds_the_whole_codebase():
    deps, svc, audit = await _with_code()
    v = await svc.view(project_id="p1", phase=6, user=make_user("DEV"))
    assert v["status"] == "implemented" and v["checkpoint"] == "code" and v["generatedCount"] == v["fileCount"] == len(deps.db.plans[-1]["structure"]["files"])
    names = [c["name"] for c in v["tree"]["children"]]
    assert "src" in names and "README.md" in names and all(f["generated"] and f["artefactId"] for f in v["files"])
    data, name = await svc.zip(project_id="p1", phase=6, user=make_user("DEV"))
    z = zipfile.ZipFile(io.BytesIO(data))
    assert name == "Quicksilver-EIP.zip" and "Quicksilver-EIP/src/app/main.py" in z.namelist() and z.testzip() is None
    assert len(z.namelist()) == v["fileCount"] and "code.zip.downloaded" in audit.events


async def test_reset_discards_the_structure_for_writers_but_not_after_the_commit():
    deps, svc, audit = await _with_code()
    with pytest.raises(SdlcError, match="write permission"):
        await svc.reset(project_id="p1", phase=6, user=make_user("QA"))
    out = await svc.reset(project_id="p1", phase=6, user=make_user("PROJECT_MANAGER"), reason="wrong layout")
    assert out["status"] == "none" and (await svc.view(project_id="p1", phase=6, user=make_user("DEV")))["status"] == "none"
    assert "code.structure.reset" in audit.events
    deps2, svc2, _ = await _with_code()
    deps2.db.plans[-1]["committed_at"] = datetime.now(UTC)
    with pytest.raises(SdlcError, match="already committed"):
        await svc2.reset(project_id="p1", phase=6, user=make_user("PROJECT_MANAGER"))


async def test_other_stages_and_the_disabled_switch_are_unaffected():
    _, svc, _ = await _with_code()
    assert (await svc.view(project_id="p1", phase=1, user=make_user("DEV"))) == {"enabled": False, "status": "none"}
    _, off, _ = await _with_code(enabled=False)
    assert (await off.view(project_id="p1", phase=6, user=make_user("DEV")))["enabled"] is False
    assert await off.structure_pending("p1", 6, STAGE) is None


# ---------------------------------------------------------------- the real database
ADMIN = os.environ.get("TEST_DATABASE_URL")


@pytest.mark.skipif(not ADMIN, reason="TEST_DATABASE_URL not set")
async def test_code_plans_version_supersede_and_approve_in_postgres():
    import asyncpg

    from app.repos.pg import Database

    name = f"cp_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(ADMIN)
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    db = Database(f"{ADMIN.rpartition('/')[0]}/{name}")
    await db.connect()
    try:
        await db.run_migrations(Path(__file__).resolve().parents[3] / "infra" / "migrations")
        await db.pool.execute("INSERT INTO users (id,email,display_name,role,password_hash) VALUES ('u','u@t','U','PROJECT_MANAGER','x')")
        p = await db.create_project(name="Cp", created_by="u")
        s = cs.normalise_structure(PROPOSAL)
        a = await db.insert_code_plan(project_id=p["id"], phase=6, structure=s, meta={"branch": "b"}, artefact_id=None, proposed_by="agent")
        assert a["version"] == 1 and a["status"] == "proposed" and a["structure"]["files"][0]["path"]
        b = await db.insert_code_plan(project_id=p["id"], phase=6, structure=s, meta={"branch": "b2"}, artefact_id=None, proposed_by="agent")
        assert b["version"] == 2
        latest = await db.latest_code_plan(p["id"], 6)
        assert latest["id"] == b["id"] and latest["meta"]["branch"] == "b2"                  # the old proposal is superseded
        assert await db.approve_code_plan(a["id"], approver="x") is None                     # a superseded one cannot be approved
        ok = await db.approve_code_plan(b["id"], approver="boss@x", comments="good")
        assert ok["status"] == "approved" and ok["decided_by"] == "boss@x"
        assert await db.approve_code_plan(b["id"], approver="again") is None                 # decided once
        await db.mark_code_plan_implemented(b["id"])
        await db.mark_code_plan_committed(b["id"], "sha1")
        done = await db.latest_code_plan(p["id"], 6)
        assert done["implemented_at"] and done["committed_at"] and done["commit_ref"] == "sha1"
        await db.supersede_code_plan(b["id"])
        assert await db.latest_code_plan(p["id"], 6) is None
    finally:
        await db.close()
        admin = await asyncpg.connect(ADMIN)
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()
