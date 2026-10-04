import json

import pytest

from app.devmind_index.builder import IndexBuilder, tier_for_rank
from app.devmind_index.integrity import check_integrity
from app.devmind_index.packet import Budget, Query, assemble
from app.devmind_index.publish import build_publish_action
from app.devmind_index.reader import IndexReader
from app.devmind_index.render import canonical_json
from app.devmind_index.schema import MANIFEST_PATH, SprintDigest, Tier
from app.devmind_index.workspace import IndexDriftError, IndexWorkspace
from app.services.content_store import FilesystemContentStore

from .devmind_synthetic import MemStore, build_project, charter, release, sprint

pytestmark = pytest.mark.asyncio


def _new(store=None):
    ws = IndexWorkspace(store or MemStore(), "p1")
    return ws, IndexBuilder(ws)


async def test_render_is_deterministic_and_round_trips():
    d = sprint(7)
    text = canonical_json(d)
    assert canonical_json(SprintDigest.model_validate_json(text)) == text  # render → parse → render identical
    assert text == canonical_json(sprint(7))
    store = await _built(3)
    assert "DO NOT EDIT" in store.data[next(k for k in store.data if k.endswith("charter.md"))]


async def _built(n: int) -> MemStore:
    store = MemStore()
    ws, b = _new(store)
    await build_project(b, n)
    return store


async def test_same_inputs_give_byte_identical_workspaces():
    a, b = await _built(9), await _built(9)
    assert a.data == b.data


async def test_context_packet_stays_flat_as_the_project_grows():
    ws, b = _new()
    sizes = {}
    await b.update(charter=charter())
    for n in range(1, 61):
        d = sprint(n)
        rid = d.release
        group = [x for x in range(1, n + 1) if sprint(x).release == rid]
        await b.update(sprint=d, release=release(rid, [f"S-{x:03d}" for x in group], n % 4 == 0),
                       current_release=rid, current_sprint=d.id)
        if n in (5, 15, 30, 45, 60):
            p = await assemble(ws, Query(components=("auth", "orders"), keywords=("checkout",)))
            sizes[n] = p.tokens
            assert p.tokens <= Budget().total
    # grows briefly while history fills the budget, then is flat: never proportional to project age
    assert sizes[60] <= Budget().total
    assert sizes[60] <= sizes[30] * 1.15 and sizes[45] <= sizes[30] * 1.15


async def test_packet_is_deterministic_and_explains_itself():
    ws, b = _new()
    await build_project(b, 20)
    q = Query(story_ids=("ST-005-2",), components=("payments",))
    p1, p2 = await assemble(ws, q), await assemble(ws, q)
    assert p1.text == p2.text and p1.tokens == p2.tokens
    refs = [i.ref for i in p1.items]
    assert "charter" in refs
    assert any(i.layer == "L2" and "S-005" in i.ref for i in p1.items)  # requested story's sprint loaded
    assert all(i.why for i in p1.items)


async def test_small_budget_drops_by_priority_and_reports_it():
    ws, b = _new()
    await build_project(b, 20)
    p = await assemble(ws, Query(components=("auth", "orders", "payments", "catalog")), Budget(2000, 600, 700, 300))
    assert p.tokens <= 3600
    assert p.dropped and all(d.why == "budget" for d in p.dropped)


async def test_old_releases_are_archived_and_still_answerable():
    ws, b = _new()
    await build_project(b, 60)
    m = await ws.manifest()
    tiers = await IndexReader(ws).release_tiers()
    assert tiers[sprint(1).release] is Tier.ARCHIVED
    assert tiers[sprint(60).release] is Tier.ACTIVE
    # archived release: per-sprint files left the tree, one consolidated file took their place
    assert ".devmind/sprints/S-001/digest.json" not in m.files
    assert any(p.startswith(".devmind/archive/") for p in m.files)
    # the tree stays small: live per-sprint files exist only for non-archived releases
    live = [e for e in m.files.values() if e.kind == "sprint" and e.path.endswith("digest.json")]
    assert len(live) < 60
    # an archived story is still found through the lookup and the archive file
    lk = await IndexReader(ws).lookup()
    assert lk["stories"]["ST-001-0"]["tier"] == "archived"
    p = await assemble(ws, Query(story_ids=("ST-001-0",)))
    assert any("S-001" in i.ref and "archived" in i.ref for i in p.items)
    assert max(e.bytes for e in m.files.values()) < 256 * 1024  # every file stays small
    assert await check_integrity(ws) == []


async def test_tier_rule_never_archives_an_open_release():
    assert tier_for_rank(0, False) is Tier.ACTIVE
    assert tier_for_rank(1, True) is Tier.ACTIVE
    assert tier_for_rank(3, True) is Tier.CLOSED
    assert tier_for_rank(4, True) is Tier.ARCHIVED
    assert tier_for_rank(9, False) is Tier.CLOSED  # open releases are never archived


async def test_hand_edit_is_detected_not_trusted():
    store = MemStore()
    ws, b = _new(store)
    await build_project(b, 6)
    key = ws.key(".devmind/charter.json")
    store.data[key] = store.data[key].replace("Shop", "Hacked")
    store.data[ws.key(".devmind/notes.txt")] = "stray"
    kinds = {(d.path, d.kind) for d in await ws.verify()}
    assert (".devmind/charter.json", "modified") in kinds and (".devmind/notes.txt", "orphan") in kinds
    with pytest.raises(IndexDriftError):
        await ws.read(".devmind/charter.json")
    await b.update(charter=charter(), heal=True)  # regenerating from sources overwrites the edit
    assert await check_integrity(ws) == []


async def test_crash_before_manifest_is_detected_and_heals_on_rerun():
    store = MemStore()
    ws, b = _new(store)
    await build_project(b, 5)
    before = (await ws.manifest()).generation
    store.fail_on = "manifest.json"
    with pytest.raises(RuntimeError):
        await b.update(sprint=sprint(6), release=release(sprint(6).release, ["S-005", "S-006"], False))
    assert (await ws.manifest()).generation == before            # the commit marker never moved
    assert await ws.verify()                                      # half-written state is DETECTED, not silent
    store.fail_on = None
    await b.update(sprint=sprint(6), release=release(sprint(6).release, ["S-005", "S-006"], False), heal=True)
    assert await check_integrity(ws) == []                        # idempotent re-run heals it


async def test_unchanged_inputs_write_nothing():
    ws, b = _new()
    await build_project(b, 4)
    gen = (await ws.manifest()).generation
    res = await b.update(sprint=sprint(4))
    assert not res.written and not res.removed
    assert (await ws.manifest()).generation == gen


async def test_publish_diff_commits_only_what_changed():
    ws, b = _new()
    await build_project(b, 3)
    act = await build_publish_action(ws)
    assert act["args"]["branch"] == "devmind/index" and act["args"]["strategy"] == "index-branch"  # B is the default
    paths = [f["path"] for f in act["args"]["files"]]
    assert MANIFEST_PATH in paths and ".devmind/charter.json" in paths
    committed = json.loads(next(f["content"] for f in act["args"]["files"] if f["path"] == MANIFEST_PATH))
    assert committed["publishedCommit"] is None and committed["publishedFiles"] == {}  # deterministic copy
    await ws.mark_published("abc123")
    assert await build_publish_action(ws) is None                 # nothing pending
    await b.update(sprint=sprint(4), release=release(sprint(4).release, ["S-001", "S-002", "S-003", "S-004"], True))
    act2 = await build_publish_action(ws, strategy="default-branch", default_branch="trunk")
    assert act2["args"]["branch"] == "trunk"
    changed = {f["path"] for f in act2["args"]["files"]}
    assert ".devmind/sprints/S-004/digest.json" in changed and ".devmind/charter.json" not in changed


async def test_works_on_the_real_filesystem_store(tmp_path):
    fs = FilesystemContentStore(str(tmp_path))
    ws = IndexWorkspace(fs, "proj")
    b = IndexBuilder(ws)
    await build_project(b, 12)
    assert (tmp_path / "content-store/proj/_devmind/.devmind/manifest.json").is_file()  # mirrors the repo layout on disk
    assert await check_integrity(ws) == []
    assert any(k.endswith("lookup.json") for k in await fs.list_prefix("content-store/proj/_devmind"))
    p = await assemble(ws, Query(components=("auth",)))
    assert p.tokens > 0
    await fs.delete("content-store/proj/_devmind/.devmind/README.md")
    assert [d.kind for d in await ws.verify()] == ["missing"]


async def test_lookup_stays_bounded():
    ws, b = _new()
    await build_project(b, 60)
    lk = await IndexReader(ws).lookup()
    assert len(lk["keywords"]) <= 3000 and all(len(v) <= 20 for v in lk["keywords"].values())


async def test_unrecoverable_drift_is_refused_not_papered_over():
    store = MemStore()
    ws, b = _new(store)
    await build_project(b, 6)
    key = ws.key(".devmind/sprints/S-002/digest.json")
    store.data[key] = store.data[key].replace("Deliver", "Tampered")
    with pytest.raises(IndexDriftError):  # S-002 is not supplied, so there is nothing trustworthy to rebuild it from
        await b.update(sprint=sprint(7), release=release(sprint(7).release, ["S-005", "S-006", "S-007"], False))
