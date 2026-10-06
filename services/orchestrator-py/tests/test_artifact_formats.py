"""Per-artifact output format: the catalog and its rules, storage, reference isolation, and generation
that gives each artifact its own layout instead of one choice for the whole stage."""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agents import phase_agents as pa
from app.agents.phase_agents import PhaseAgentResult
from app.domain.errors import SdlcError
from app.domain.models import AgentState, ContextArtifact
from app.services import artifact_formats as af

from .conftest import FakeAudit


# ================================================================== the catalog
@pytest.mark.parametrize("output,kind,sources,files", [
    ("PRD", "narrative", ["system", "attachment", "formwork"], ["markdown", "docx", "pdf", "html"]),
    ("hld", "narrative", ["system", "attachment", "formwork"], ["markdown", "docx", "pdf", "html"]),
    ("Epic & Story backlog", "narrative", ["system", "attachment", "formwork"], ["markdown", "docx", "pdf", "html"]),  # unknown -> prose
    ("EPIC", "structured", ["system", "formwork"], ["markdown"]),
    ("ADR", "structured", ["system", "formwork"], ["markdown"]),
    ("OPENAPI", "code", ["system", "formwork"], ["yaml", "json"]),
    ("DBML", "code", ["system", "formwork"], ["dbml"]),
    ("COMPONENT_DIAGRAM", "diagram", ["system"], ["svg"]),
])
def test_each_kind_allows_only_what_makes_sense(output, kind, sources, files):
    assert af.kind_of(output) == kind and af.sources_for(output) == sources and af.file_types_for(output) == files


def test_catalog_lists_each_output_once_with_labelled_file_types():
    cat = af.catalog(["PRD", "prd", "OPENAPI"])
    assert [c["type"] for c in cat] == ["PRD", "OPENAPI"]
    assert cat[0]["fileTypes"][0] == {"value": "markdown", "label": "Markdown", "native": True}
    assert {"value": "docx", "label": "Word (.docx)", "native": False} in cat[0]["fileTypes"]


# ================================================================== validation
OUT = ["PRD", "EPIC", "OPENAPI", "ARCH_DIAGRAM"]


def test_valid_formats_are_normalised_and_defaults_are_not_stored():
    got = af.validate_formats({
        "prd": {"source": "attachment", "refId": "a1", "fileType": "docx"},
        "OPENAPI": {"source": "formwork", "refId": "f1", "fileType": "yaml"},     # yaml is native -> not stored
        "EPIC": {"source": "system"},                                              # all defaults -> dropped
    }, OUT)
    assert got == {"PRD": {"source": "attachment", "refId": "a1", "fileType": "docx"},
                   "OPENAPI": {"source": "formwork", "refId": "f1"}}
    assert af.validate_formats(None, OUT) == {} and af.validate_formats({}, OUT) == {}


@pytest.mark.parametrize("raw,why", [
    ({"HLD": {"source": "system"}}, "not an artifact this stage produces"),
    ({"ARCH_DIAGRAM": {"source": "attachment", "refId": "a"}}, "cannot use 'attachment'"),
    ({"OPENAPI": {"source": "attachment", "refId": "a"}}, "cannot use 'attachment'"),
    ({"EPIC": {"source": "attachment", "refId": "a"}}, "cannot use 'attachment'"),
    ({"PRD": {"source": "attachment"}}, "choose which attached file"),
    ({"PRD": {"source": "formwork"}}, "choose which template"),
    ({"PRD": {"source": "magic", "refId": "x"}}, "cannot use 'magic'"),
    ({"PRD": {"source": "system", "fileType": "exe"}}, "cannot be delivered as 'exe'"),
    ({"EPIC": {"fileType": "docx"}}, "cannot be delivered as 'docx'"),
    ({"PRD": "docx"}, "must be an object"),
    (["PRD"], "keyed by artifact type"),
])
def test_invalid_formats_are_refused_with_the_reason(raw, why):
    with pytest.raises(SdlcError, match=why):
        af.validate_formats(raw, OUT)


def test_stored_values_are_read_tolerantly():
    assert af.parse_formats(json.dumps({"prd": {"source": "attachment", "refId": "a", "junk": 1}, "bad": 5})) == \
        {"PRD": {"source": "attachment", "refId": "a"}}
    assert af.parse_formats("{not json") == {} and af.parse_formats(None) == {}


def test_helpers_pick_out_what_generation_needs():
    fm = {"PRD": {"source": "attachment", "refId": "a"}, "EPIC": {"source": "formwork", "refId": "f"},
          "HLD": {"source": "attachment", "refId": "b"}}
    assert af.attachment_layout_types(fm) == ["PRD", "HLD"]
    assert af.attachment_layout_types(fm, included=["prd"]) == ["PRD"]
    assert af.formwork_selection(fm) == {"EPIC": "f"}


# ================================================================== reference isolation
def _svc(attachments=(), formworks=()):
    from app.services.chat import ChatService

    class Db:
        async def get_attachments_by_ids(self, ids):
            return [a for a in attachments if a["id"] in ids]

        async def get_formworks_by_ids(self, ids):
            return [f for f in formworks if f["id"] in ids]

    svc = ChatService.__new__(ChatService)
    svc._db = Db()
    return svc


ATT = {"id": "a1", "project_id": "p1", "phase": 1, "filename": "template.docx", "is_text": True, "storage_key": "k"}


async def test_a_format_may_only_point_at_this_projects_own_stage_files_and_templates():
    svc = _svc([ATT, {**ATT, "id": "a2", "project_id": "other"}, {**ATT, "id": "a3", "phase": 2},
                {**ATT, "id": "a4", "is_text": False}],
               [{"id": "f1", "project_id": None, "artefact_type": "PRD", "name": "Std PRD"},
                {"id": "f2", "project_id": "other", "artefact_type": "PRD", "name": "Theirs"},
                {"id": "f3", "project_id": "p1", "artefact_type": "HLD", "name": "HLD tpl"}])
    await svc._assert_format_refs("p1", 1, {"PRD": {"source": "attachment", "refId": "a1"}})
    await svc._assert_format_refs("p1", 1, {"PRD": {"source": "formwork", "refId": "f1"}})      # platform template ok
    for bad, why in [({"PRD": {"source": "attachment", "refId": "a2"}}, "not part of this stage"),
                     ({"PRD": {"source": "attachment", "refId": "a3"}}, "not part of this stage"),
                     ({"PRD": {"source": "attachment", "refId": "nope"}}, "not part of this stage"),
                     ({"PRD": {"source": "attachment", "refId": "a4"}}, "could not be read"),
                     ({"PRD": {"source": "formwork", "refId": "f2"}}, "not available to this project"),
                     ({"PRD": {"source": "formwork", "refId": "f3"}}, "is a template for HLD")]:
        with pytest.raises(SdlcError, match=why):
            await svc._assert_format_refs("p1", 1, bad)


def test_the_plan_screen_gets_options_and_current_choice_per_artifact():
    svc = _svc()
    stage = {"outputs": ["PRD", "OPENAPI"], "template": 1}
    fws = [{"id": "f1", "name": "Std PRD", "artefactType": "PRD", "scope": "platform", "outputFormat": "markdown",
            "analysis": {"sections": ["Goals", "Scope"]}},
           {"id": "f2", "name": "Team PRD", "artefactType": "PRD", "scope": "project", "outputFormat": "markdown",
            "analysis": {}}]
    atts = [{"id": "a1", "filename": "spec.docx", "is_text": True}, {"id": "a2", "filename": "logo.png", "is_text": False}]
    cat = {c["type"]: c for c in svc._format_catalog(stage, fws, atts, {"PRD": {"source": "attachment", "refId": "a1", "fileType": "docx"}})}
    prd = cat["PRD"]
    assert prd["houseTemplate"] == "Team PRD"                                     # project template shadows platform
    assert [f["id"] for f in prd["formworks"]] == ["f1", "f2"] and prd["formworks"][0]["sections"] == ["Goals", "Scope"]
    assert prd["attachments"] == [{"id": "a1", "filename": "spec.docx"}]          # unreadable files are not offered
    assert prd["selected"] == {"source": "attachment", "refId": "a1", "fileType": "docx"}
    assert cat["OPENAPI"]["attachments"] == [] and cat["OPENAPI"]["selected"]["fileType"] == "yaml"


# ================================================================== real Postgres: stored and preserved
ADMIN = os.environ.get("TEST_DATABASE_URL")
needs_pg = pytest.mark.skipif(not ADMIN, reason="TEST_DATABASE_URL not set")


@needs_pg
async def test_formats_persist_and_survive_rewrites_that_do_not_know_about_them():
    import asyncpg

    from app.repos.pg import Database

    name = f"fmt_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(ADMIN)
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    db = Database(f"{ADMIN.rpartition('/')[0]}/{name}")
    await db.connect()
    try:
        await db.run_migrations(Path(__file__).resolve().parents[3] / "infra" / "migrations")
        await db.pool.execute("INSERT INTO users (id,email,display_name,role,password_hash) VALUES ('u','u@t','U','PROJECT_MANAGER','x')")
        p = await db.create_project(name="Fmt", created_by="u")
        base = dict(project_id=p["id"], phase=1, prompt_overlay="x", referenced_artifact_ids=[], attachment_ids=[],
                    formwork_ids=[], origin="new", updated_by="u")
        fm = {"PRD": {"source": "attachment", "refId": "a1", "fileType": "docx"}}
        await db.upsert_stage_plan(**base, artifact_formats=fm)
        assert af.parse_formats((await db.get_stage_plan(p["id"], 1))["artifact_formats"]) == fm
        await db.upsert_stage_plan(**{**base, "prompt_overlay": "edited"})              # e.g. answering a clarification
        row = await db.get_stage_plan(p["id"], 1)
        assert row["prompt_overlay"] == "edited" and af.parse_formats(row["artifact_formats"]) == fm   # preserved
        await db.upsert_stage_plan(**base, artifact_formats={})                           # explicit reset
        assert af.parse_formats((await db.get_stage_plan(p["id"], 1))["artifact_formats"]) == {}
        await db.upsert_stage_plan(**{**base, "phase": 2})                                # brand new row
        assert af.parse_formats((await db.get_stage_plan(p["id"], 2))["artifact_formats"]) == {}
    finally:
        await db.close()
        admin = await asyncpg.connect(ADMIN)
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


# ================================================================== generation
class _Db:
    def __init__(self):
        self.parts: dict[str, dict] = {}
        self.artefacts: list[dict] = []

    async def upsert_generation_part(self, *, field, status, error=None, value_json=None, partial_text=None, **_):
        self.parts[field] = {"status": status, "error": error, "value": value_json}

    async def latest_artefact_version(self, *a):
        return None

    async def insert_artefact(self, **kw):
        self.artefacts.append(kw)


class _Llm:
    """generate_stream that writes a recognisable document and records what it was asked."""

    def __init__(self, fail_for=()):
        self.calls: list[dict] = []
        self.fail_for = tuple(fail_for)

    async def generate_stream(self, *, intent, messages, on_delta, tag, **kw):
        self.calls.append({"tag": tag, "system": messages[0]["content"], "user": messages[1]["content"], **kw})
        if any(f in tag for f in self.fail_for):
            raise SdlcError("PROVIDER_ERROR", "model unavailable")
        for chunk in ("# Doc\n\n", "body text"):
            on_delta(chunk)
        return SimpleNamespace(content="# Doc\n\nbody text", provider="bedrock", model="m",
                               usage={"promptTokens": 3, "completionTokens": 4})

    async def generate(self, **kw):
        return SimpleNamespace(content="x", provider="bedrock")


def _deps(llm=None):
    content = SimpleNamespace(mode="fs", put=lambda k, c: asyncio.sleep(0))
    rag = SimpleNamespace(index_artifact=lambda *a: asyncio.sleep(0))
    return SimpleNamespace(llm=llm or _Llm(), db=_Db(), audit=FakeAudit(), content=content, rag=rag, canon=None,
                           settings=SimpleNamespace(PUBLISH_ON_APPROVAL=True, CONTEXT_TOKEN_THRESHOLD=16_000,
                                                    PHASE_MAX_TOKENS=4000))


LAYOUT = {"PRD": {"name": "Acme PRD sample.docx", "text": "# Acme PRD\n## 1. Vision\n## 2. Personas\n| Persona | Need |"}}


def _state(**kw):
    base = dict(project_id="p1", session_id="s", current_phase=1, stage_template=1, stage_name="Requirements",
                user_input="Build a payments platform", extra_context="### Attachment - reqs.pdf\nMUST support SSO",
                artifact_formats={"PRD": {"source": "attachment", "refId": "a1"}}, format_layouts=LAYOUT)
    base.update(kw)
    return AgentState(**base)


class _Runner:
    """Stands in for the standard stage runner: records the state it was given."""

    def __init__(self):
        self.states: list[AgentState] = []

    async def __call__(self, deps, state, emit):
        self.states.append(state)
        return PhaseAgentResult(summary="Standard artifacts made.", gate_status="PENDING_REVIEW", new_artifacts=[
            ContextArtifact(phase=1, type="EPIC", title="Epics", summary="e")])


def test_only_prose_artifacts_the_stage_produces_and_keeps_are_written_separately():
    assert pa.layout_doc_types(_state()) == ["PRD"]
    assert pa.layout_doc_types(_state(artifact_formats={"EPIC": {"source": "attachment", "refId": "a"}},
                                      format_layouts={"EPIC": {"name": "x", "text": "y"}})) == []      # not prose
    assert pa.layout_doc_types(_state(format_layouts={})) == []                                          # layout unreadable
    assert pa.layout_doc_types(_state(artifact_formats={"HLD": {"source": "attachment", "refId": "a"}},
                                      format_layouts={"HLD": {"name": "x", "text": "y"}})) == []         # stage 1 makes no HLD
    scoped = _state(user_input="x\n\n## Production scope (confirmed by the reviewer)\n- Do NOT produce: PRD.\n")
    assert pa.layout_doc_types(scoped) == []                                                              # reviewer dropped it


async def test_the_prd_follows_the_attached_layout_while_the_other_artifacts_keep_their_own():
    deps, runner, events = _deps(), _Runner(), []
    res = await pa._run_with_layout_docs(deps, _state(), events.append, runner, ["PRD"])
    # the standard runner ran for everything else and was told to skip the PRD (exactly one copy)
    assert len(runner.states) == 1 and "PRD" in runner.states[0].skip_types
    assert pa.run_scope(runner.states[0])["exclude"].count("PRD") >= 1
    # the PRD was written as its own document from the chosen layout, with the source material
    (call,) = deps.llm.calls
    assert "Acme PRD sample.docx" in call["system"] and "## 2. Personas" in call["system"]
    assert "MUST support SSO" in call["system"] and "layout guide only" in call["system"].lower() or "structure" in call["system"]
    assert call["tag"] == "stage1_layout_prd"
    types = sorted(a.type for a in res.new_artifacts)
    assert types == ["EPIC", "PRD"] and res.gate_status == "PENDING_REVIEW"
    saved = {a["type_"]: a for a in deps.db.artefacts}
    assert saved["PRD"]["title"] == "Product Requirements Document"              # same title -> a new VERSION of the PRD
    assert deps.db.parts["document:PRD"]["status"] == "done"
    assert "Written as their own documents" in res.summary and "Acme PRD sample.docx" in res.summary
    # live view: the document streams under its own part, so it gets its own tab
    assert any(e.get("type") == "content_delta" and e.get("part") == "document:PRD" for e in events)


async def test_the_prd_document_is_queued_for_confluence_like_the_standard_one():
    deps = _deps()
    res = await pa._run_with_layout_docs(deps, _state(), lambda e: None, _Runner(), ["PRD"])
    tools = [a["tool"] for a in (res.publish_actions or [])]
    assert "confluence_publish_prd" in tools                                       # despite PRD being skipped by the runner


async def test_when_every_artifact_follows_a_layout_the_standard_runner_does_not_run():
    runner = _Runner()
    st = _state(stage_template=2, current_phase=2, stage_name="Solution",
                artifact_formats={t: {"source": "attachment", "refId": "a"} for t in ("HLD",)},
                format_layouts={"HLD": {"name": "arch.docx", "text": "# Architecture"}},
                user_input="x\n\n## Production scope (confirmed by the reviewer)\n- Produce ONLY these artifacts: HLD.\n- Do NOT produce: ADR, STRUCTURIZR_DSL, CLOUDCRAFT_JSON, ARCH_DIAGRAM, HLD_DIAGRAM.\n")
    res = await pa._run_with_layout_docs(_deps(), st, lambda e: None, runner, ["HLD"])
    assert runner.states == [] and [a.type for a in res.new_artifacts] == ["HLD"]


async def test_a_failing_document_does_not_sink_the_stage_and_can_be_retriggered():
    deps, runner, events = _deps(_Llm(fail_for=("layout_prd",))), _Runner(), []
    res = await pa._run_with_layout_docs(deps, _state(), events.append, runner, ["PRD"])
    assert [a.type for a in res.new_artifacts] == ["EPIC"]                          # the rest of the stage is intact
    assert deps.db.parts["document:PRD"]["status"] == "failed"
    assert any(e.get("part") == "document:PRD" and e.get("status") == "failed" for e in events)
    assert any("Retrigger it" in e.get("label", "") for e in events)

    # retrigger just that document: the standard runner does NOT run again
    deps2, runner2 = _deps(), _Runner()
    res2 = await pa._run_with_layout_docs(deps2, _state(retrigger_fields=["document:PRD"]), lambda e: None, runner2, ["PRD"])
    assert runner2.states == [] and [a.type for a in res2.new_artifacts] == ["PRD"]


async def test_retriggering_ordinary_fields_regenerates_them_without_redoing_the_document():
    deps, runner = _deps(), _Runner()
    res = await pa._run_with_layout_docs(deps, _state(retrigger_fields=["epics"]), lambda e: None, runner, ["PRD"])
    assert deps.llm.calls == [] and runner.states[0].retrigger_fields == ["epics"] and "PRD" in runner.states[0].skip_types
    assert [a.type for a in res.new_artifacts] == ["EPIC"]


async def test_if_nothing_could_be_generated_the_stage_fails_loudly():
    with pytest.raises(SdlcError, match="none of the requested documents"):
        await pa._run_with_layout_docs(_deps(_Llm(fail_for=("layout_prd",))),
                                       _state(user_input="x\n\n## Production scope (confirmed by the reviewer)\n- Produce ONLY these artifacts: PRD.\n- Do NOT produce: EPIC, FEATURE, USER_STORY.\n"),
                                       lambda e: None, _Runner(), ["PRD"])


async def test_run_phase_agent_routes_layout_stages_and_leaves_ordinary_ones_alone(monkeypatch):
    seen: list[str] = []

    async def fake_layouts(deps, state, emit, runner, types):
        seen.append(f"layout:{types}")
        return PhaseAgentResult(summary="L", gate_status="PENDING_REVIEW", new_artifacts=[])

    async def fake_standard(deps, state, emit, runner):
        seen.append("standard")
        return PhaseAgentResult(summary="S", gate_status="PENDING_REVIEW", new_artifacts=[])

    monkeypatch.setattr(pa, "_run_with_layout_docs", fake_layouts)
    monkeypatch.setattr(pa, "_run_standard", fake_standard)
    await pa.run_phase_agent(_deps(), _state(), lambda e: None)
    await pa.run_phase_agent(_deps(), _state(artifact_formats={}, format_layouts={}), lambda e: None)
    assert seen == ["layout:['PRD']", "standard"]


async def test_the_old_stage_wide_token_still_works_when_no_per_artifact_formats_exist(monkeypatch):
    called: list[str] = []

    async def fake_custom(deps, state, emit):
        called.append("legacy")
        return PhaseAgentResult(summary="legacy", gate_status="PENDING_REVIEW", new_artifacts=[])

    monkeypatch.setattr(pa, "_run_custom_format", fake_custom)
    legacy = _state(artifact_formats={}, format_layouts={},
                    user_input="x\n\n## Production scope (confirmed by the reviewer)\n- Follow this output format: ATTACHED_DOCUMENT — a.docx\n")
    await pa.run_phase_agent(_deps(), legacy, lambda e: None)
    assert called == ["legacy"]


# ================================================================== templates (formworks) chosen per artifact
class _FwDb:
    def __init__(self, rows):
        self.rows = rows

    async def list_formworks(self, project_id):
        return sorted((r for r in self.rows if r["project_id"] in (None, project_id)), key=lambda r: r["project_id"] is None)

    async def get_formworks_by_ids(self, ids):
        return [r for r in self.rows if r["id"] in ids]


def _fw(id_, type_, name, project_id=None):
    return {"id": id_, "project_id": project_id, "artefact_type": type_, "output_format": "markdown", "name": name,
            "template": f"# {name}\n## Section A", "analysis": {"sections": ["Section A"], "placeholders": []},
            "storage_key": None, "created_by": "u", "created_at": None, "updated_at": None, "version": 1}


async def test_a_template_chosen_for_one_artifact_replaces_its_default_and_others_are_unchanged():
    from app.services.formworks import FormworkService

    svc = FormworkService.__new__(FormworkService)
    svc._db = _FwDb([_fw("f1", "PRD", "Default PRD"), _fw("f2", "PRD", "Team PRD", "p1"), _fw("f3", "HLD", "Default HLD"),
                     _fw("fx", "PRD", "Foreign PRD", "other")])
    svc._row = lambda r: {"id": r["id"], "artefactType": r["artefact_type"], "outputFormat": r["output_format"],
                          "name": r["name"], "template": r["template"], "analysis": r["analysis"], "scope": "x"}
    base = await svc.render_block("p1", ["PRD", "HLD"])
    assert "Default HLD" in base and "Team PRD" in base                          # project template shadows platform by default

    pinned = await svc.render_block("p1", ["PRD", "HLD"], selected={"PRD": "f1"})
    assert "Default PRD" in pinned and "Team PRD" not in pinned and "Default HLD" in pinned

    assert "PRD" not in (await svc.render_block("p1", ["PRD", "HLD"], skip={"PRD"})).replace("Default HLD", "")  # follows a document instead
    safe = await svc.render_block("p1", ["PRD"], selected={"PRD": "fx"})         # another project's template: ignored
    assert "Foreign PRD" not in safe and "Team PRD" in safe
    wrong = await svc.render_block("p1", ["PRD"], selected={"PRD": "f3"})         # a template for a different type: ignored
    assert "Default HLD" not in wrong


# ================================================================== resolving the choices when a run starts
async def test_choices_are_resolved_into_layout_text_and_stale_ones_fall_back_to_the_standard():
    from app.services.chat import ChatService

    long_doc = "# Acme PRD\n" + "\n".join(f"## {i}. Section {i}\n" + ("filler " * 400) for i in range(1, 15))

    class Db:
        async def get_attachments_by_ids(self, ids):
            rows = {"a1": {"id": "a1", "project_id": "p1", "phase": 1, "filename": "acme.docx", "is_text": True, "storage_key": "k1"},
                    "a2": {"id": "a2", "project_id": "p1", "phase": 1, "filename": "gone.docx", "is_text": True, "storage_key": "k2"},
                    "a3": {"id": "a3", "project_id": "other", "phase": 1, "filename": "theirs.docx", "is_text": True, "storage_key": "k3"}}
            return [rows[i] for i in ids if i in rows]

    class Content:
        async def get(self, key):
            return {"k1": long_doc, "k2": "", "k3": "SECRET"}.get(key)

    svc = ChatService.__new__(ChatService)
    svc._db, svc._deps = Db(), SimpleNamespace(content=Content())
    events: list[dict] = []
    fm = {"PRD": {"source": "attachment", "refId": "a1", "fileType": "docx"},
          "HLD": {"source": "attachment", "refId": "a2"}, "LLD": {"source": "attachment", "refId": "a3"},
          "OPENAPI": {"source": "formwork", "refId": "f9"}}
    out = await svc._resolve_formats("p1", 1, fm, events.append)
    assert set(out["format_layouts"]) == {"PRD"} and out["format_layouts"]["PRD"]["name"] == "acme.docx"
    assert len(out["format_layouts"]["PRD"]["text"]) <= 14_500 and "Acme PRD" in out["format_layouts"]["PRD"]["text"]
    assert out["formwork_selection"] == {"OPENAPI": "f9"}
    assert set(out["artifact_formats"]) == {"PRD", "OPENAPI"}                    # HLD (deleted) and LLD (foreign) dropped
    warned = [e["label"] for e in events if "no longer available" in e.get("label", "")]
    assert len(warned) == 2 and not any("SECRET" in json.dumps(out) for _ in [0])
