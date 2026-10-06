"""Two-step code generation helpers: structure validation, tree, batching, batch checks, zip."""
from __future__ import annotations

import io
import zipfile

import pytest

from app.agents.schemas import CodeStructureOutput
from app.domain.errors import SdlcError
from app.services import code_structure as cs


def _out(files, dirs=None):
    return CodeStructureOutput(summary="s", conventions=["snake_case modules"], directories=dirs or [], files=files,
                               branch="feature/x", commitMessage="m", prTitle="t", prBody="b")


F = lambda p, purpose="does a thing", **kw: {"path": p, "purpose": purpose, **kw}  # noqa: E731


@pytest.mark.parametrize("bad", ["/etc/passwd", "../x.py", "a/../b.py", "a//b.py", "", "x" * 300, "a/b$.py", "C:\\x"])
def test_unsafe_paths_are_refused(bad):
    with pytest.raises(SdlcError):
        cs.clean_path(bad)


def test_paths_are_normalised():
    assert cs.clean_path("./src\\app/main.py") == "src/app/main.py"


def test_structure_is_validated_normalised_and_complete():
    s = cs.normalise_structure(
        _out([F("src/app/main.py", layer="api"), F("src/app/services/orders.py"), F("tests/test_orders.py"), F("README.md", kind="docs")]),
        extra_files=[{"path": "pyproject.toml", "purpose": "tooling and coverage gate"}])
    paths = [f["path"] for f in s["files"]]
    assert paths == sorted(paths, key=str.lower) and "pyproject.toml" in paths
    assert {d["path"] for d in s["directories"]} == {"src", "src/app", "src/app/services", "tests"}      # parents are listed
    assert next(f for f in s["files"] if f["path"] == "tests/test_orders.py")["kind"] == "test"           # inferred from the path
    assert next(f for f in s["files"] if f["path"] == "pyproject.toml")["kind"] == "config"


def test_duplicates_clashes_and_oversize_are_refused():
    with pytest.raises(SdlcError, match="twice"):
        cs.normalise_structure(_out([F("a.py"), F("A.py")]))
    with pytest.raises(SdlcError, match="both a file and a directory"):
        cs.normalise_structure(_out([F("src"), F("src/x.py")]))
    with pytest.raises(SdlcError, match="at most"):
        cs.normalise_structure(_out([F(f"f{i}.py") for i in range(cs.MAX_FILES + 1)]))
    # the platform's own file wins silently over a duplicate the model also proposed
    s = cs.normalise_structure(_out([F("pyproject.toml", "model's version"), F("a.py")]), extra_files=[{"path": "pyproject.toml", "purpose": "platform"}])
    assert [f["path"] for f in s["files"]].count("pyproject.toml") == 1


def test_the_hash_changes_only_when_the_file_plan_changes():
    a = cs.normalise_structure(_out([F("a.py"), F("b.py")]))
    b = cs.normalise_structure(_out([F("b.py"), F("a.py")]))
    c = cs.normalise_structure(_out([F("a.py"), F("b.py", "different purpose")]))
    assert cs.structure_hash(a) == cs.structure_hash(b) != cs.structure_hash(c)


def test_tree_and_markdown_show_directories_files_and_purposes():
    s = cs.normalise_structure(_out([F("src/main.py", "entry point"), F("src/util/io.py"), F("README.md")],
                                    [{"path": "src", "purpose": "application code"}]))
    t = cs.build_tree(s, generated={"src/main.py"})
    assert [c["name"] for c in t["children"]] == ["src", "README.md"]                       # directories first
    src = t["children"][0]
    assert src["purpose"] == "application code" and [c["name"] for c in src["children"]] == ["util", "main.py"]
    assert next(c for c in src["children"] if c["name"] == "main.py")["generated"] is True
    md = cs.render_markdown(s, {"branch": "feature/x", "commitMessage": "m"}, version=2)
    assert "(v2)" in md and "├── " in md and "└── " in md and "| `src/main.py` | entry point |" in md and "feature/x" in md


def test_files_are_batched_by_top_level_directory_and_written_files_are_skipped():
    s = cs.normalise_structure(_out([F(f"src/m{i}.py") for i in range(8)] + [F("tests/test_a.py"), F("README.md")]))
    batches = cs.make_batches(s, max_files=6)
    assert [len(b.paths) for b in batches] == [1, 6, 2, 1]                                  # '.' , src(6+2), tests
    assert all(p.startswith("src/") for b in batches[1:3] for p in b.paths)
    assert sum(len(b.paths) for b in cs.make_batches(s, skip={"README.md", "src/m0.py"})) == 8


def test_a_generated_batch_is_held_to_the_agreed_files():
    acc, missing, extra = cs.check_batch(["src/a.py", "src/b.py"], [
        {"path": "src/a.py", "content": "x = 1"}, {"path": "SRC/B.py", "content": ""}, {"path": "src/evil.py", "content": "y"},
        {"path": "../escape.py", "content": "z"}])
    assert acc == {"src/a.py": "x = 1"} and missing == ["src/b.py"] and sorted(extra) == ["../escape.py", "src/evil.py"]


def test_the_zip_holds_every_file_under_one_project_folder():
    data = cs.build_zip({"src/main.py": "print(1)", "README.md": "# hi"}, root="Quicksilver EIP!", extras={"STRUCTURE.md": "tree"})
    z = zipfile.ZipFile(io.BytesIO(data))
    assert sorted(z.namelist()) == ["Quicksilver-EIP/README.md", "Quicksilver-EIP/STRUCTURE.md", "Quicksilver-EIP/src/main.py"]
    assert z.read("Quicksilver-EIP/src/main.py") == b"print(1)" and z.testzip() is None
    with pytest.raises(SdlcError):
        cs.build_zip({"../x": "y"}, root="r")
