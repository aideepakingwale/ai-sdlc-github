"""Two-step code generation helpers: the proposed repository structure, its validation, the tree the reviewer
sees, how the approved structure is split into implementation batches, how a generated batch is checked
against it, and the .zip of the finished codebase. Pure (no I/O) except `build_zip`'s in-memory archive.

Step 1 proposes {directories, files[path, purpose, kind, layer]} + naming conventions; a human approves it.
Step 2 writes exactly those files - a file outside the approved structure is never accepted.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass
from typing import Any

from ..domain.errors import SdlcError

MAX_FILES = 300
MAX_PATH = 200
_SAFE = re.compile(r"^[A-Za-z0-9._@+\-/ ]+$")
_TEST_HINT = re.compile(r"(^|/)(tests?|__tests__|spec|specs)(/|$)|(\.|_)(test|spec)\.[A-Za-z0-9]+$|(^|/)test_[^/]+$", re.I)


def clean_path(raw: str) -> str:
    """A repository-relative POSIX path, or raise. No absolute paths, no `..`, no odd characters."""
    p = (raw or "").strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    if not p or p.startswith("/") or len(p) > MAX_PATH or not _SAFE.match(p):
        raise SdlcError("VALIDATION_FAILED", f"'{raw}' is not a valid repository-relative path")
    parts = p.split("/")
    if any(s in ("", ".", "..") or s.startswith(" ") or s.endswith(" ") for s in parts):
        raise SdlcError("VALIDATION_FAILED", f"'{raw}' is not a valid repository-relative path")
    return "/".join(parts)


def is_test_path(path: str) -> bool:
    return bool(_TEST_HINT.search(path))


def normalise_structure(out: Any, *, extra_files: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Validate and normalise a CodeStructureOutput (or dict) into the stored structure.
    Paths are cleaned, case-insensitive duplicates and file/directory clashes are refused, every file's
    parent directory is listed, and the platform's deterministic files (`extra_files`: quality-gate config)
    are folded in so the reviewer approves them too."""
    data = out.model_dump() if hasattr(out, "model_dump") else dict(out)
    files: dict[str, dict[str, Any]] = {}

    def add(f: dict[str, Any], *, platform: bool = False) -> None:
        path = clean_path(f["path"])
        key = path.lower()
        if key in files and not platform:
            raise SdlcError("VALIDATION_FAILED", f"the structure lists '{path}' twice")
        kind = "config" if platform else (f.get("kind") or "source")
        files[key] = {"path": path, "purpose": (f.get("purpose") or "").strip()[:240] or "(no purpose given)",
                      "kind": "test" if kind == "source" and is_test_path(path) else kind,
                      "layer": "quality" if platform else (f.get("layer") or ""), "covers": list(f.get("covers") or [])[:12]}

    for f in data.get("files", []):
        add(f)
    for e in extra_files or []:                      # the platform's own files win over a same-path proposal
        add(e, platform=True)
    if len(files) > MAX_FILES:
        raise SdlcError("VALIDATION_FAILED", f"the structure has {len(files)} files; at most {MAX_FILES} are allowed")
    if len(files) < 2:
        raise SdlcError("VALIDATION_FAILED", "the structure must list at least two files")
    dirs: dict[str, str] = {}
    for d in data.get("directories", []):
        dp = clean_path(d["path"]).rstrip("/")
        dirs[dp.lower()] = d.get("purpose", "")
        dirs[dp.lower() + "\0name"] = dp
    names = {k[:-5]: v for k, v in dirs.items() if k.endswith("\0name")}
    purposes = {k: v for k, v in dirs.items() if not k.endswith("\0name")}
    for f in files.values():
        parent = f["path"].rsplit("/", 1)[0] if "/" in f["path"] else ""
        while parent:
            names.setdefault(parent.lower(), parent)
            parent = parent.rsplit("/", 1)[0] if "/" in parent else ""
    clash = sorted(n for n in names if n in files)
    if clash:
        raise SdlcError("VALIDATION_FAILED", f"'{names[clash[0]]}' is listed as both a file and a directory")
    directories = [{"path": names[k], "purpose": purposes.get(k, "")} for k in sorted(names)]
    return {
        "summary": (data.get("summary") or "").strip()[:1200],
        "conventions": [str(c).strip()[:300] for c in data.get("conventions", []) if str(c).strip()][:30],
        "directories": directories,
        "files": sorted(files.values(), key=lambda f: f["path"].lower()),
    }


def structure_hash(structure: dict[str, Any]) -> str:
    """Stable fingerprint of the file set (paths + purposes) - recorded when it is approved."""
    canon = json.dumps([[f["path"], f["purpose"], f["kind"]] for f in structure["files"]], sort_keys=True)
    return hashlib.sha256(canon.encode()).hexdigest()[:16]


def build_tree(structure: dict[str, Any], generated: set[str] | None = None) -> dict[str, Any]:
    """Nested {name, path, type: dir|file, purpose, kind, generated, children[]} for the explorer."""
    root: dict[str, Any] = {"name": "", "path": "", "type": "dir", "purpose": "", "children": []}
    index: dict[str, dict[str, Any]] = {"": root}
    purposes = {d["path"].lower(): d.get("purpose", "") for d in structure.get("directories", [])}

    def ensure_dir(path: str) -> dict[str, Any]:
        if path in index:
            return index[path]
        parent = ensure_dir(path.rsplit("/", 1)[0] if "/" in path else "")
        node = {"name": path.rsplit("/", 1)[-1], "path": path, "type": "dir", "purpose": purposes.get(path.lower(), ""), "children": []}
        parent["children"].append(node)
        index[path] = node
        return node

    for d in structure.get("directories", []):
        ensure_dir(d["path"])
    for f in structure["files"]:
        parent = ensure_dir(f["path"].rsplit("/", 1)[0] if "/" in f["path"] else "")
        parent["children"].append({"name": f["path"].rsplit("/", 1)[-1], "path": f["path"], "type": "file", "purpose": f["purpose"],
                                   "kind": f["kind"], "layer": f.get("layer", ""), "generated": f["path"] in (generated or set())})

    def order(node: dict[str, Any]) -> None:
        node["children"].sort(key=lambda c: (c["type"] != "dir", c["name"].lower()))
        for c in node["children"]:
            if c["type"] == "dir":
                order(c)
    order(root)
    return root


def render_markdown(structure: dict[str, Any], meta: dict[str, Any] | None = None, *, version: int = 1) -> str:
    """The CODE_STRUCTURE artifact body: summary, conventions, directory tree and a purpose table."""
    lines = [f"# Code structure & file plan (v{version})", ""]
    if structure.get("summary"):
        lines += [structure["summary"], ""]
    if meta and meta.get("branch"):
        lines += [f"**Branch:** `{meta['branch']}` · **Commit message:** {meta.get('commitMessage', '')}", ""]
    lines += ["## Naming & layout conventions", "", *[f"- {c}" for c in structure.get("conventions", [])], "", "## Directory structure", "", "```text"]

    def walk(node: dict[str, Any], prefix: str) -> None:
        kids = node["children"]
        for i, c in enumerate(kids):
            last = i == len(kids) - 1
            lines.append(f"{prefix}{'└── ' if last else '├── '}{c['name']}{'/' if c['type'] == 'dir' else ''}")
            if c["type"] == "dir":
                walk(c, prefix + ("    " if last else "│   "))
    lines.append("./")
    walk(build_tree(structure), "")
    lines += ["```", "", "## Files and their purpose", "", "| File | Purpose | Kind | Layer |", "| --- | --- | --- | --- |"]
    for f in structure["files"]:
        lines.append(f"| `{f['path']}` | {f['purpose'].replace('|', '/')} | {f['kind']} | {f.get('layer', '')} |")
    return "\n".join(lines) + "\n"


@dataclass
class Batch:
    index: int
    paths: list[str]


def make_batches(structure: dict[str, Any], *, skip: set[str] | None = None, max_files: int = 6) -> list[Batch]:
    """Group the not-yet-written files into batches of related files (same top-level directory first) so each
    model call has coherent context and a bounded output."""
    todo = [f for f in structure["files"] if f["path"] not in (skip or set())]
    groups: dict[str, list[str]] = {}
    for f in todo:
        head = f["path"].split("/", 1)[0] if "/" in f["path"] else "."
        groups.setdefault(head, []).append(f["path"])
    batches: list[Batch] = []
    for head in sorted(groups):
        paths = groups[head]
        for i in range(0, len(paths), max_files):
            batches.append(Batch(len(batches), paths[i:i + max_files]))
    return batches


def check_batch(batch_paths: list[str], produced: list[dict[str, str]]) -> tuple[dict[str, str], list[str], list[str]]:
    """Compare what the model returned with what was agreed. Returns (accepted {path: content}, missing, unexpected).
    A file outside the batch is never accepted; an empty file counts as missing."""
    wanted = {p.lower(): p for p in batch_paths}
    accepted: dict[str, str] = {}
    unexpected: list[str] = []
    for f in produced:
        try:
            p = clean_path(f["path"])
        except SdlcError:
            unexpected.append(str(f.get("path")))
            continue
        canon = wanted.get(p.lower())
        if canon is None:
            unexpected.append(p)
        elif (f.get("content") or "").strip():
            accepted[canon] = f["content"]
    missing = [p for p in batch_paths if p not in accepted]
    return accepted, missing, unexpected


def build_zip(files: dict[str, str], *, root: str, extras: dict[str, str] | None = None) -> bytes:
    """An in-memory .zip with every file under one `root/` folder (so unzipping gives a single project directory)."""
    buf = io.BytesIO()
    safe_root = re.sub(r"[^A-Za-z0-9._-]+", "-", root).strip("-") or "codebase"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, content in sorted({**(extras or {}), **files}.items()):
            z.writestr(f"{safe_root}/{clean_path(path)}", content)
    return buf.getvalue()
