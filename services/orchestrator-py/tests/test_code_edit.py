"""The code assistant: tools and scope on an overlay, the agent loop, review/apply/undo for uploaded and generated code."""
from __future__ import annotations

import pytest

from app.domain.errors import SdlcError
from app.services import code_edit as ce
from app.services.code_edit import AgentStep, CodeEditService, Overlay, ToolCall, Tools, Workspace

from .conftest import FakeAudit, make_user


class MemWs(Workspace):
    label = "test code"

    def __init__(self, files: dict[str, str]):
        self.f = dict(files)

    async def files(self):
        return {p: len(t) for p, t in self.f.items()}

    async def read(self, path):
        return self.f.get(path)


FILES = {"app/service.py": "def total(items):\n    return sum(items)\n", "app/util.py": "X = 1\n", "lib/other.py": "Y = 2\n", "data.json": "{}"}


def tools(targets, files=None):
    ov = Overlay(MemWs(files or FILES), targets)
    return Tools(ov), ov


async def call(t: Tools, **kw):
    return await t.run(ToolCall(**kw))


async def test_read_numbers_lines_and_paginates():
    t, _ = tools([])
    text, ok, _ = await call(t, tool="read_file", path="app/service.py")
    assert ok and text.startswith("1\tdef total")
    text, ok, _ = await call(t, tool="read_file", path="app/service.py", start=2, end=2)
    assert text == "2\t    return sum(items)"
    _, ok, _ = await call(t, tool="read_file", path="nope.py")
    assert not ok


async def test_list_and_search():
    t, _ = tools([])
    text, ok, _ = await call(t, tool="list_dir", path="")
    assert text.splitlines() == ["app/", "lib/", "data.json"]
    text, _, _ = await call(t, tool="search", pattern=r"sum\(")
    assert text == "app/service.py:2: return sum(items)"
    text, ok, _ = await call(t, tool="search", pattern="(")
    assert not ok and "regular expression" in text


async def test_edit_requires_exact_unique_match():
    t, ov = tools([])
    _, ok, _ = await call(t, tool="edit_file", path="app/service.py", old_string="nothing like it", new_string="x")
    assert not ok
    ov.base.f["app/util.py"] = "A\nA\n"
    ov._cache.clear(); ov._base_files = None
    text, ok, _ = await call(t, tool="edit_file", path="app/util.py", old_string="A", new_string="B")
    assert not ok and "2 times" in text
    _, ok, _ = await call(t, tool="edit_file", path="app/util.py", old_string="A", new_string="B", replace_all=True)
    assert ok and ov.changes["app/util.py"]["after"] == "B\nB\n" and ov.changes["app/util.py"]["before"] == "A\nA\n"


async def test_edits_stay_inside_the_selection():
    t, ov = tools(["app/service.py", "lib"])
    _, ok, _ = await call(t, tool="edit_file", path="app/util.py", old_string="X = 1", new_string="X = 2")
    assert not ok and "app/util.py" not in ov.changes                      # unselected file is read-only
    _, ok, _ = await call(t, tool="edit_file", path="lib/other.py", old_string="Y = 2", new_string="Y = 3")
    assert ok                                                               # inside a selected folder
    _, ok, _ = await call(t, tool="write_file", path="lib/new.py", content="Z = 1\n")
    assert ok and ov.changes["lib/new.py"]["action"] == "create"            # new file in a selected folder
    _, ok, _ = await call(t, tool="write_file", path="app/helper.py", content="H = 1\n")
    assert ok                                                               # beside a selected file
    _, ok, _ = await call(t, tool="write_file", path="elsewhere/x.py", content="no")
    assert not ok
    _, ok, _ = await call(t, tool="delete_file", path="app/util.py")
    assert not ok


async def test_empty_selection_means_whole_workspace():
    t, _ = tools([])
    _, ok, _ = await call(t, tool="edit_file", path="app/util.py", old_string="X = 1", new_string="X = 2")
    assert ok


async def test_paths_cannot_escape():
    t, _ = tools([])
    text, ok, _ = await call(t, tool="read_file", path="../etc/passwd")
    assert not ok and "not a path inside the project" in text


async def test_secrets_are_masked_in_new_text():
    t, ov = tools([])
    await call(t, tool="write_file", path="cfg.py", content='KEY = "AKIAABCDEFGHIJKLMNOP"\n')
    assert "AKIAABCDEFGHIJKLMNOP" not in ov.changes["cfg.py"]["after"]


async def test_second_edit_keeps_the_original_before_and_noop_clears():
    t, ov = tools([])
    await call(t, tool="edit_file", path="app/util.py", old_string="X = 1", new_string="X = 2")
    await call(t, tool="edit_file", path="app/util.py", old_string="X = 2", new_string="X = 3")
    assert ov.changes["app/util.py"] == {"action": "modify", "before": "X = 1\n", "after": "X = 3\n"}
    await call(t, tool="edit_file", path="app/util.py", old_string="X = 3", new_string="X = 1")
    assert "app/util.py" not in ov.changes                                  # edited back to the original


async def test_created_then_deleted_leaves_nothing_and_changed_count_is_capped():
    t, ov = tools([])
    await call(t, tool="write_file", path="n.py", content="1")
    await call(t, tool="delete_file", path="n.py")
    assert "n.py" not in ov.changes
    for i in range(ce.MAX_CHANGED):
        await call(t, tool="write_file", path=f"g{i}.py", content="1")
    text, ok, _ = await call(t, tool="write_file", path="one_more.py", content="1")
    assert not ok and "At most" in text


async def test_check_syntax():
    t, ov = tools([])
    await call(t, tool="write_file", path="bad.py", content="def x(:\n")
    text, ok, _ = await call(t, tool="check_syntax", path="bad.py")
    assert not ok and "line 1" in text
    _, ok, _ = await call(t, tool="check_syntax", path="app/service.py")
    assert ok


def test_diff_counts():
    d, a, r = ce.diff_of("a\nb\n", "a\nc\nd\n", "f.txt")
    assert (a, r) == (2, 1) and "+++ b/f.txt" in d


# ---------------------------------------------------------------- service
class Row(dict):
    pass


class Db:
    def __init__(self, uploaded: dict[str, str]):
        self.code = {p: {"id": f"id-{i}", "path": p, "content": t, "size_bytes": len(t)} for i, (p, t) in enumerate(uploaded.items())}
        self.sessions: dict[str, dict] = {}
        self.indexed: list[str] = []

    async def get_project(self, pid):
        return {"id": pid, "created_by": "u-SUPER_ADMIN", "tech_stack": "Python"}

    async def list_codebase_files(self, pid):
        return list(self.code.values())

    async def get_codebase_file(self, pid, fid):
        return next((r for r in self.code.values() if r["id"] == fid), None)

    async def upsert_codebase_file(self, *, project_id, path, content, uploaded_by):
        self.code[path] = {"id": self.code.get(path, {}).get("id", f"id-{path}"), "path": path, "content": content, "size_bytes": len(content)}

    async def delete_codebase_file(self, pid, path):
        self.code.pop(path, None)

    async def save_code_edit(self, s):
        self.sessions[s["id"]] = {**s}

    async def get_code_edit(self, pid, sid):
        s = self.sessions.get(sid)
        return {**s} if s else None

    async def list_code_edits(self, pid, uid, scope=None, limit=30):
        return []


class Llm:
    """Plays a fixed script of turns."""

    def __init__(self, steps):
        self.steps, self.i, self.seen = list(steps), 0, []

    async def generate_json(self, *, messages, schema, **kw):
        self.seen.append(messages)
        step = self.steps[min(self.i, len(self.steps) - 1)]
        self.i += 1
        from types import SimpleNamespace
        return step, SimpleNamespace(usage={"promptTokens": 10, "completionTokens": 5}, model="m", provider="p")


class Rag:
    def __init__(self, db):
        self.db = db

    async def index_codebase_file(self, pid, path, content):
        self.db.indexed.append(path)


class Authz:
    async def assert_project_access(self, pid, user):
        return None


class Wf:
    async def view(self, pid):
        return {"stages": [{"seq": 6, "template": 6}]}


class Chat:
    allowed = True

    async def can_write_stage(self, pid, phase, user):
        return self.allowed


class Dyn:
    status = "PENDING_REVIEW"

    async def get_phase_state(self, pid, phase):
        return {"status": self.status}


def svc(db, llm, chat=None, dyn=None):
    return CodeEditService(db, None, Rag(db), FakeAudit(), Authz(), llm, chat or Chat(), Wf(), dyn or Dyn())


def edit_step(path, old, new):
    return AgentStep(message="editing", calls=[ToolCall(tool="edit_file", path=path, old_string=old, new_string=new)])


DONE = AgentStep(done=True, summary="All set")


async def run(s, user, **kw):
    ev = []
    await s.stream(project_id="p1", user=user, emit=ev.append, **{"scope": "uploaded", "targets": [], "session_id": None, **kw})
    return ev


async def test_uploaded_flow_propose_apply_undo():
    db = Db({"app/service.py": "def total(items):\n    return sum(items)\n"})
    s = svc(db, Llm([edit_step("app/service.py", "sum(items)", "sum(i for i in items if i)"), DONE]))
    user = make_user("SUPER_ADMIN")
    ev = await run(s, user, prompt="ignore None", targets=["app/service.py"])
    done = ev[-1]
    assert done["type"] == "done" and done["summary"] == "All set"
    assert [c["path"] for c in done["changes"]] == ["app/service.py"] and done["changes"][0]["added"] == 1
    assert "sum(items)" in db.code["app/service.py"]["content"]          # nothing written yet
    out = await s.apply("p1", done["sessionId"], user)
    assert out["applied"] == ["app/service.py"] and out["status"] == "applied"
    assert "if i" in db.code["app/service.py"]["content"] and db.indexed == ["app/service.py"]
    await s.revert("p1", done["sessionId"], user)
    assert db.code["app/service.py"]["content"] == "def total(items):\n    return sum(items)\n"


async def test_partial_apply_and_discard():
    db = Db({"a.py": "A = 1\n", "b.py": "B = 1\n"})
    llm = Llm([AgentStep(calls=[ToolCall(tool="edit_file", path="a.py", old_string="1", new_string="2"),
                                ToolCall(tool="edit_file", path="b.py", old_string="1", new_string="2")]), DONE])
    s = svc(db, llm)
    user = make_user("SUPER_ADMIN")
    sid = (await run(s, user, prompt="bump"))[-1]["sessionId"]
    out = await s.apply("p1", sid, user, ["a.py"])
    assert out["remaining"] == ["b.py"] and db.code["a.py"]["content"] == "A = 2\n" and db.code["b.py"]["content"] == "B = 1\n"
    await s.discard("p1", sid, user)
    assert (await s.get("p1", sid, user))["changes"] == []


async def test_apply_refuses_when_the_file_changed_meanwhile():
    db = Db({"a.py": "A = 1\n"})
    s = svc(db, Llm([edit_step("a.py", "1", "2"), DONE]))
    user = make_user("SUPER_ADMIN")
    sid = (await run(s, user, prompt="bump"))[-1]["sessionId"]
    db.code["a.py"]["content"] = "A = 9\n"                               # someone else edited it
    with pytest.raises(SdlcError) as e:
        await s.apply("p1", sid, user)
    assert e.value.code == "GATE_CONFLICT" and db.code["a.py"]["content"] == "A = 9\n"


async def test_follow_up_continues_the_session_and_sees_pending_changes():
    db = Db({"a.py": "A = 1\n"})
    llm = Llm([edit_step("a.py", "1", "2"), DONE, edit_step("a.py", "2", "3"), DONE])
    s = svc(db, llm)
    user = make_user("SUPER_ADMIN")
    sid = (await run(s, user, prompt="bump"))[-1]["sessionId"]
    done = (await run(s, user, prompt="more", session_id=sid))[-1]
    assert done["sessionId"] == sid
    assert done["changes"][0]["diff"].count("+A = 3") == 1 and "-A = 1" in done["changes"][0]["diff"]   # against the original
    assert "already proposed" in llm.seen[2][1]["content"] and "bump" in llm.seen[2][1]["content"]


async def test_a_failed_call_is_reported_to_the_model_not_raised():
    db = Db({"a.py": "A = 1\n"})
    llm = Llm([edit_step("a.py", "missing text", "x"), DONE])
    s = svc(db, llm)
    ev = await run(s, make_user("SUPER_ADMIN"), prompt="x")
    assert any(e["type"] == "tool" and e["status"] == "error" for e in ev) and ev[-1]["changes"] == []
    assert "ERROR" in llm.seen[1][-1]["content"]


async def test_step_limit_ends_the_loop():
    db = Db({"a.py": "A = 1\n"})
    forever = AgentStep(calls=[ToolCall(tool="list_dir", path="")])
    ev = await run(svc(db, Llm([forever])), make_user("SUPER_ADMIN"), prompt="x")
    assert "step limit" in ev[-1]["summary"] and sum(1 for e in ev if e["type"] == "tool" and e["status"] == "done") == ce.MAX_STEPS


async def test_permissions_and_inputs():
    db = Db({"a.py": "A = 1\n"})
    chat = Chat()
    s = svc(db, Llm([DONE]), chat=chat)
    user = make_user("SUPER_ADMIN")
    chat.allowed = False
    with pytest.raises(SdlcError) as e:
        await run(s, user, prompt="x")
    assert e.value.code == "FORBIDDEN"
    chat.allowed = True
    with pytest.raises(SdlcError):
        await run(s, user, prompt="  ")
    with pytest.raises(SdlcError) as e:
        await run(s, user, prompt="x", targets=["ghost.py"])
    assert e.value.code == "NOT_FOUND"
    with pytest.raises(SdlcError):
        await run(s, user, prompt="x", scope="elsewhere")
    other = make_user("PROJECT_MANAGER")
    sid = (await run(s, user, prompt="x"))[-1]["sessionId"]
    with pytest.raises(SdlcError):
        await s.apply("p1", sid, other)                                  # sessions belong to the person who started them


async def test_generated_code_cannot_be_edited_after_the_stage_is_approved():
    dyn = Dyn()
    dyn.status = "APPROVED"
    s = svc(Db({}), Llm([DONE]), dyn=dyn)
    with pytest.raises(SdlcError) as e:
        await run(s, make_user("SUPER_ADMIN"), prompt="x", scope="generated")
    assert e.value.code == "GATE_CONFLICT"


class Content:
    mode = "filesystem"

    def __init__(self):
        self.k: dict[str, str] = {}

    async def put(self, key, body):
        self.k[key] = body

    async def get(self, key):
        return self.k.get(key)


class GenDb(Db):
    def __init__(self, files: dict[str, str], content: Content):
        super().__init__({})
        self.rows = []
        for i, (p, t) in enumerate(files.items()):
            key = f"content-store/p1/phase-6/{p}"
            content.k[key] = t
            self.rows.append({"id": f"a{i}", "type": "UNIT_TESTS" if p.startswith("tests/") else "APP_CODE", "title": p, "content": t[:2000],
                              "storage_key": key, "is_latest": True, "phase": 6, "version": 1})
        self.plan = {"id": "cp1", "structure": {"files": [{"path": p, "purpose": "x", "kind": "source"} for p in files]}}

    async def list_phase_artefacts(self, pid, phase):
        return [r for r in self.rows]

    async def update_artefact_content(self, aid, content):
        r = next(r for r in self.rows if r["id"] == aid)
        r["content"], r["version"] = content, r["version"] + 1
        return r["version"]

    async def insert_artefact(self, *, project_id, phase, type_, title, content, url, storage_key=None, storage_mode=None, artefact_id=None, lineage_id=None, version=1):
        self.rows.append({"id": artefact_id, "type": type_, "title": title, "content": content[:2000], "storage_key": storage_key, "is_latest": True, "phase": phase, "version": 1})
        return artefact_id

    async def supersede_artefact(self, aid):
        next(r for r in self.rows if r["id"] == aid)["is_latest"] = False

    async def latest_code_plan(self, pid, phase):
        return self.plan

    async def update_code_plan_structure(self, plan_id, structure):
        self.plan["structure"] = structure


async def test_generated_code_apply_and_undo_keep_artefacts_and_the_tree_in_step():
    content = Content()
    db = GenDb({"src/a.py": "A = 1\n", "src/b.py": "B = 1\n"}, content)
    s = CodeEditService(db, content, Rag(db), FakeAudit(), Authz(), Llm([
        AgentStep(calls=[ToolCall(tool="edit_file", path="src/a.py", old_string="1", new_string="2"),
                         ToolCall(tool="write_file", path="src/test_a.py", content="def test(): pass\n"),
                         ToolCall(tool="delete_file", path="src/b.py")]), DONE]), Chat(), Wf(), Dyn())
    user = make_user("SUPER_ADMIN")
    ev = await run(s, user, prompt="refactor", scope="generated", targets=["src"])
    assert {c["path"]: c["action"] for c in ev[-1]["changes"]} == {"src/a.py": "modify", "src/test_a.py": "create", "src/b.py": "delete"}
    assert content.k["content-store/p1/phase-6/src/a.py"] == "A = 1\n"           # nothing written before apply
    sid = ev[-1]["sessionId"]
    await s.apply("p1", sid, user)
    assert content.k["content-store/p1/phase-6/src/a.py"] == "A = 2\n"
    live = {r["title"]: r for r in db.rows if r["is_latest"]}
    assert set(live) == {"src/a.py", "src/test_a.py"} and live["src/test_a.py"]["type"] == "UNIT_TESTS"
    assert [f["path"] for f in db.plan["structure"]["files"]] == ["src/a.py", "src/test_a.py"]
    await s.revert("p1", sid, user)
    assert content.k["content-store/p1/phase-6/src/a.py"] == "A = 1\n"
