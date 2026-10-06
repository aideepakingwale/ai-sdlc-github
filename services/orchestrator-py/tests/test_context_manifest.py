"""The context manifest: layers, item statuses, relationships, and the preview/actual diff."""
from __future__ import annotations

from app.domain.models import ContextArtifact
from app.services.context_manifest import build_manifest, diff_manifests, tokens_of

STAGE = {"name": "Requirements", "persona": "Business Analyst", "template": 1, "outputs": ["PRD", "EPIC"]}
PROJECT = {"name": "Pay", "description": "Payments platform", "tech_stack": "Python 3.12 + FastAPI", "tech_stack_source": "ta"}


def _manifest(**kw):
    base = dict(
        mode="preview", phase=2, stage={**STAGE, "template": 2, "name": "Solution"}, project=PROJECT,
        overlay={"promptOverlay": "Design the platform\n\n## Production scope (x)\n- Produce ONLY: HLD"},
        context_artifacts=[
            ContextArtifact(phase=1, type="PRD", title="Product Requirements Document", summary="s", content="x" * 5000),
            ContextArtifact(phase=1, type="EPIC", title="Epics", summary="only a summary", content=None),
            ContextArtifact(phase=1, type="ADR", title="ADR-1", summary="s", content="short"),
        ],
        snippets=[{"id": "k1", "title": "Security standard", "source": "global", "content": "k" * 300, "score": 0.42}],
        canon_block="canon text", formworks=[
            {"id": "f1", "name": "House PRD", "artefactType": "PRD", "scope": "platform", "chars": 900, "status": "excluded", "note": "Set aside"},
            {"id": "f2", "name": "House HLD", "artefactType": "HLD", "scope": "project", "chars": 700}],
        attached=[
            {"kind": "attachment", "id": "a1", "label": "sample.docx", "filename": "sample.docx", "chars": 80_000, "totalChars": 743_000, "status": "condensed"},
            {"kind": "attachment", "id": "a2", "label": "logo.png", "chars": 0, "status": "excluded", "note": "Binary"},
            {"kind": "reference", "id": "r1", "label": "@HLD: Old HLD", "chars": 8000, "totalChars": 20_000, "status": "condensed", "phase": 2, "artifactType": "HLD"}],
        traits={"has_ui": False, "has_api": True}, has_codebase=True, codebase_files=12,
        formats={"PRD": {"source": "attachment", "refId": "a1"}},
    )
    base.update(kw)
    return build_manifest(**base)


def test_every_layer_is_present_with_items_and_totals():
    m = _manifest()
    ids = [layer["id"] for layer in m["layers"]]
    assert ids == ["instructions", "project", "canon", "upstream", "input", "attached", "retrieved"]
    by = {layer["id"]: layer for layer in m["layers"]}
    assert any("Quality bar" in i["label"] for i in by["instructions"]["items"])
    assert m["totals"]["tokens"] == tokens_of(m["totals"]["chars"]) and m["totals"]["items"] == sum(len(layer["items"]) for layer in m["layers"])
    assert m["mode"] == "preview" and m["stage"] == "Solution"


def test_item_statuses_say_how_much_of_each_thing_the_stage_actually_has():
    m = _manifest()
    items = {i["label"]: i for layer in m["layers"] for i in layer["items"]}
    prd = items["[Stage 1] PRD: Product Requirements Document"]
    assert (prd["status"], prd["chars"], prd["totalChars"]) == ("condensed", 1200, 5000)
    assert items["[Stage 1] EPIC: Epics"]["status"] == "summarised"
    assert items["[Stage 1] ADR: ADR-1"]["status"] == "full"
    doc = items["sample.docx"]
    assert (doc["status"], doc["chars"], doc["totalChars"]) == ("condensed", 80_000, 743_000)
    assert items["logo.png"]["status"] == "excluded"
    assert items["Template - House PRD (PRD)"]["status"] == "excluded"          # set aside: the PRD follows sample.docx
    assert items["Reviewer instructions"]["chars"] == len("Design the platform")   # the scope directive is not counted as the reviewer's words
    assert items["Technology stack: Python 3.12 + FastAPI"]["source"]["decidedBy"] == "ta"


def test_excluded_items_cost_nothing_and_have_no_edge_into_the_prompt():
    m = _manifest()
    assert m["totals"]["excluded"] == 2
    edge_from = {e["from"] for e in m["edges"] if e["to"] == "prompt"}
    assert "attached:attachment:a2" not in edge_from and "attached:attachment:a1" in edge_from
    attached = next(layer for layer in m["layers"] if layer["id"] == "attached")
    assert attached["chars"] == 88_000                                          # 80,000 + 8,000; the excluded logo adds none


def test_relationships_show_which_file_each_artifact_follows():
    m = _manifest()
    follows = [e for e in m["edges"] if e["label"] == "layout followed"]
    assert [(e["from"], e["to"]) for e in follows] == [("attached:attachment:a1", "output:PRD")]
    assert {o["type"] for o in m["outputs"]} == {"PRD", "EPIC"}
    assert any(e["from"] == "prompt" and e["to"] == "output:EPIC" for e in m["edges"])


def test_an_undecided_stack_and_no_instructions_are_shown_as_such():
    m = _manifest(project={**PROJECT, "tech_stack": ""}, overlay={"promptOverlay": ""})
    items = {i["id"]: i for layer in m["layers"] for i in layer["items"]}
    assert items["project:stack"]["status"] == "excluded" and "undecided" in items["project:stack"]["label"]
    assert items["input:instruction"]["status"] == "excluded"


def test_the_diff_between_two_runs_lists_what_was_added_removed_and_resized():
    old = _manifest()
    new = _manifest(attached=[{"kind": "attachment", "id": "a1", "label": "sample.docx", "chars": 20_000, "totalChars": 743_000, "status": "condensed"},
                              {"kind": "attachment", "id": "a3", "label": "extra.pdf", "chars": 500, "status": "full"}])
    d = diff_manifests(old, new)
    assert [x["label"] for x in d["added"]] == ["extra.pdf"]
    assert {x["label"] for x in d["removed"]} == {"logo.png", "@HLD: Old HLD"}
    assert d["changed"][0]["label"] == "sample.docx" and d["changed"][0]["to"]["chars"] == 20_000
    assert diff_manifests(None, new) == {"added": [], "removed": [], "changed": []}


# ---------------------------------------------------------------- the manifest records what the prompt was built from
async def test_extra_context_records_one_item_per_attachment_reference_and_template():
    from types import SimpleNamespace

    from app.services.chat import ChatService

    big = "# Doc\n" + "\n".join(f"## S{i}\n" + "text " * 300 for i in range(60))

    class Db:
        async def get_artefacts_by_ids(self, ids):
            return [{"id": "r1", "project_id": "p", "phase": 2, "type": "HLD", "title": "Old HLD", "content": "h" * 9000, "storage_key": None}]

        async def get_attachments_by_ids(self, ids):
            return [{"id": "a1", "project_id": "p", "filename": "big.docx", "is_text": True, "storage_key": "k", "extraction": None},
                    {"id": "a2", "project_id": "p", "filename": "logo.png", "is_text": False, "storage_key": "k2", "extraction": None}]

        async def get_formworks_by_ids(self, ids):
            return [{"id": "f1", "project_id": None, "name": "House PRD", "template": "# T", "artefact_type": "PRD"}]

    class Content:
        async def get(self, key):
            return big

    svc = ChatService.__new__(ChatService)
    svc._db, svc._deps = Db(), SimpleNamespace(content=Content(), llm=SimpleNamespace())
    svc._settings = SimpleNamespace(ATTACHMENT_CONTEXT_CHARS=5_000)
    items: list = []
    text = await svc._resolve_extra_context("p", ["r1"], ["a1", "a2"], ["f1"], lambda e: None, query="design", items=items)
    by = {i["label"]: i for i in items}
    assert by["big.docx"]["status"] == "condensed" and by["big.docx"]["chars"] < by["big.docx"]["totalChars"]
    assert by["logo.png"]["status"] == "excluded"
    assert by["@HLD: Old HLD"]["status"] == "condensed" and by["@HLD: Old HLD"]["chars"] == 8000
    assert by["Template — House PRD"]["kind"] == "template"
    assert by["big.docx"]["chars"] <= len(text)                      # what the manifest claims is what the prompt holds


# ---------------------------------------------------------------- stored per run, real Postgres
import os  # noqa: E402
import uuid  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

ADMIN = os.environ.get("TEST_DATABASE_URL")


@pytest.mark.skipif(not ADMIN, reason="TEST_DATABASE_URL not set")
async def test_manifests_are_stored_per_run_trimmed_and_read_back_per_stage():
    import asyncpg

    from app.repos.pg import Database

    name = f"ctx_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(ADMIN)
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    db = Database(f"{ADMIN.rpartition('/')[0]}/{name}")
    await db.connect()
    try:
        await db.run_migrations(Path(__file__).resolve().parents[3] / "infra" / "migrations")
        await db.pool.execute("INSERT INTO users (id,email,display_name,role,password_hash) VALUES ('u','u@t','U','PROJECT_MANAGER','x')")
        p = await db.create_project(name="Ctx", created_by="u")
        for n in range(12):
            await db.insert_context_manifest(p["id"], 1, {**_manifest(), "run": n}, "u@t")
        await db.insert_context_manifest(p["id"], 2, {**_manifest(), "run": 99}, "u@t")
        runs = await db.list_context_manifests(p["id"], 1, limit=20)
        assert len(runs) == 10 and runs[0]["manifest"]["run"] == 11 and runs[0]["manifest"]["layers"]     # trimmed to the latest 10
        latest = {r["phase"]: r["manifest"]["run"] for r in await db.latest_context_manifests(p["id"])}
        assert latest == {1: 11, 2: 99}
    finally:
        await db.close()
        admin = await asyncpg.connect(ADMIN)
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def test_context_view_returns_the_live_preview_the_last_run_and_what_changed_between_runs():
    from types import SimpleNamespace

    from app.services.chat import ChatService

    stage = {"name": "Solution", "persona": "Architect", "template": 2, "outputs": ["HLD"], "seq": 2}
    run_a, run_b = _manifest(mode="actual"), _manifest(mode="actual", attached=[])

    class Db:
        async def get_project(self, pid):
            return PROJECT | {"id": pid}

        async def get_session(self, pid):
            return {"id": "s", "context_window": []}

        async def get_stage_plan(self, pid, phase):
            return None

        async def count_codebase_files(self, pid):
            return 0

        async def list_context_manifests(self, pid, phase, limit=2):
            return [{"id": "m2", "createdAt": "2026-01-02T00:00:00", "manifest": run_b},
                    {"id": "m1", "createdAt": "2026-01-01T00:00:00", "manifest": run_a}]

    class Authz:
        async def assert_project_access(self, pid, user):
            self.checked = (pid, user)

    class Rag:
        async def retrieve(self, q, pid, top_k=None, artifact_phases=None):
            return []

    svc = ChatService.__new__(ChatService)
    svc._db, svc._authz = Db(), Authz()
    svc._deps = SimpleNamespace(rag=Rag(), canon=None, formworks=None)

    async def stage_for(pid, phase):
        return {}, stage

    async def preview(project, session, stg, overlay, emit, items=None):
        items.append({"kind": "attachment", "id": "a1", "label": "spec.pdf", "chars": 100, "totalChars": 100, "status": "full"})
        return "sys", "usr", "", [], []

    async def traits(**kw):
        return {"has_api": {"value": True}}

    svc._stage_for, svc._assemble_prompt_preview, svc.resolve_project_traits = stage_for, preview, traits
    user = SimpleNamespace(email="u@t")
    out = await svc.context_view(project_id="p1", phase=2, user=user)
    assert svc._authz.checked == ("p1", user)                                   # read access is enforced
    assert out["preview"]["mode"] == "preview" and any(i["label"] == "spec.pdf" for layer in out["preview"]["layers"] for i in layer["items"])
    assert out["actual"]["runId"] == "m2" and out["actual"]["mode"] == "actual"
    assert {x["label"] for x in out["diff"]["removed"]} == {"sample.docx", "logo.png", "@HLD: Old HLD"}


# ---------------------------------------------------------------- a stage is only given what it builds on
def _art(phase, type_, title="t"):
    return ContextArtifact(phase=phase, type=type_, title=title, summary="s")


WF = {"stages": [
    {"key": "po", "seq": 1, "dependsOn": []},
    {"key": "sa", "seq": 2, "dependsOn": ["po"]},
    {"key": "ta", "seq": 3, "dependsOn": ["sa"]},
    {"key": "qa", "seq": 4, "dependsOn": ["po"]},       # a parallel branch off stage 1
]}
ALL = [_art(1, "PRD"), _art(2, "HLD"), _art(3, "LLD"), _art(4, "TEST_STRATEGY")]


def test_a_stage_sees_its_upstream_stages_only_not_its_own_output_or_later_stages():
    from app.services.chat import ChatService
    up = ChatService._upstream_window
    assert [a.type for a in up(WF, WF["stages"][1], ALL)] == ["PRD"]                    # solution: just requirements
    assert [a.type for a in up(WF, WF["stages"][2], ALL)] == ["PRD", "HLD"]              # transitive: through stage 2 back to 1
    assert [a.type for a in up(WF, WF["stages"][3], ALL)] == ["PRD"]                    # a sibling branch is not upstream
    assert up(WF, WF["stages"][0], ALL) == []                                           # the entry stage builds on nothing


def test_after_a_run_the_session_keeps_everything_and_a_rerun_replaces_instead_of_duplicating():
    from app.services.chat import ChatService
    merged = ChatService._merge_window(ALL, [_art(2, "HLD", "t"), _art(2, "ADR", "new")], 2)
    assert [(a.phase, a.type) for a in merged] == [(1, "PRD"), (3, "LLD"), (4, "TEST_STRATEGY"), (2, "HLD"), (2, "ADR")]
    assert len([a for a in merged if a.type == "HLD"]) == 1                               # no stale copy of the stage's own output


async def test_files_uploaded_after_the_plan_was_saved_still_count_as_stage_inputs():
    from types import SimpleNamespace

    from app.services.chat import ChatService

    class Db:
        async def list_attachments(self, pid, phase):
            return [{"id": "a1"}, {"id": "a2"}]

    svc = ChatService.__new__(ChatService)
    svc._db = Db()
    assert await svc._stage_attachment_ids("p", 1, ["a1"]) == ["a1", "a2"]
    assert await svc._stage_attachment_ids("p", 1, []) == ["a1", "a2"]
    assert await svc._stage_attachment_ids("p", 1, ["gone", "a2"]) == ["gone", "a2", "a1"]   # unknown ids are dropped later by project check
    del SimpleNamespace


# ---------------------------------------------------------------- retrieval must not hand a stage its own output
async def test_retrieval_counts_only_upstream_artifacts_but_never_filters_standards_or_code():
    from app.services.rag import RagService

    docs = [{"id": f"art-{p}", "title": f"[P{p}] HLD: stage {p} design", "source": "artifact", "content": "payments design", "embedding": [1.0] + [0.0] * 63}
            for p in (1, 2, 3)]
    docs += [{"id": "kb-std", "title": "AWS standard", "source": "standard", "content": "payments design", "embedding": [1.0] + [0.0] * 63},
             {"id": "code-1", "title": "[code] app.py", "source": "codebase", "content": "payments design", "embedding": [1.0] + [0.0] * 63}]

    class Db:
        async def fetch_kb_docs(self, scopes):
            return docs

    class Emb:
        def embed(self, text):
            return [1.0] + [0.0] * 63

    svc = RagService.__new__(RagService)
    svc._db, svc._settings, svc.embedder = Db(), type("S", (), {"RAG_TOP_K": 20})(), Emb()
    ids = lambda hits: sorted(h["id"] for h in hits)  # noqa: E731
    assert ids(await svc.retrieve("payments", "p")) == ["art-1", "art-2", "art-3", "code-1", "kb-std"]          # unrestricted: as before
    assert ids(await svc.retrieve("payments", "p", artifact_phases={1})) == ["art-1", "code-1", "kb-std"]       # stage 2: only stage 1's output
    assert ids(await svc.retrieve("payments", "p", artifact_phases=set())) == ["code-1", "kb-std"]              # the entry stage: no artifacts


# ---------------------------------------------------------------- the plan analysis survives a refresh
async def test_the_ai_analysis_of_a_plan_is_kept_in_the_database_when_the_cache_is_gone():
    import json
    from types import SimpleNamespace

    from app.services.chat import ChatService

    class Redis:
        def __init__(self):
            self.kv = {}

        async def get(self, k):
            return self.kv.get(k)

        async def set(self, k, v, nx=False, ex=None):
            if nx and k in self.kv:
                return None
            self.kv[k] = v
            return True

        async def delete(self, k):
            self.kv.pop(k, None)

        async def exists(self, k):
            return int(k in self.kv)

    class Db:
        def __init__(self):
            self.row = None

        async def get_stage_plan(self, pid, phase):
            return self.row

        async def set_stage_plan_intel(self, pid, phase, obj):
            self.row = {"plan_intel": json.dumps(obj), "plan_sig": "s"}          # as a JSONB column comes back

    svc = ChatService.__new__(ChatService)
    svc._redis, svc._db = Redis(), Db()
    svc._settings = SimpleNamespace(INTELLIGENT_PLANNING=True)
    analysis = {"understood": "A payments platform with idempotent retries.", "willProduce": [{"output": "PRD", "recommended": True, "include": True, "reason": "r"}]}

    async def compute(self, *, ckey, sig, **kw):
        await self._store_intel(ckey, sig, analysis)
        return {**analysis, "cached": False}

    ChatService._compute_intelligent_plan = compute            # the planner itself is not under test
    args = dict(project={"id": "p1", "tech_stack": "Py", "name": "n"}, phase=1, stage={"template": 1, "outputs": ["PRD"], "persona": "BA"},
                overlay={"promptOverlay": "Build payments"}, available_tools=[], skills=[], prior_arts=[], canon_applied=False)
    first = await svc._intelligent_plan(**args)
    assert first["cached"] is False and first["understood"].startswith("A payments")

    svc._redis.kv.clear()                                       # the cache expired / Redis restarted / page refreshed
    again = await svc._intelligent_plan(**args, allow_compute=False)
    assert again["cached"] is True and again["understood"] == analysis["understood"]          # the analysis is still there

    stale = await svc._intelligent_plan(**{**args, "overlay": {"promptOverlay": "Build payments, now with refunds"}}, allow_compute=False)
    assert stale["stale"] is True and stale["understood"] == analysis["understood"]            # edited since: shown, marked stale
    assert svc._stored_intel(svc._db.row)["plan"]["understood"] == analysis["understood"]
    assert svc._stored_intel(None) is None and svc._stored_intel({"plan_intel": "{bad"}) is None
