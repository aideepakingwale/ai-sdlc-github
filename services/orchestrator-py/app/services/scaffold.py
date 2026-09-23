"""Deterministic scaffolding — boilerplate that does not need an LLM.

Generating standard configuration in code instead of asking the model saves
output tokens on every run and removes drift: the coverage threshold in the
config always equals the configured quality gate, exactly, and the linter/
formatter config is always present and valid. Pure functions — trivially unit
tested, and free of any provider/rate-limit exposure.
"""
from __future__ import annotations

import re


def detect_stack(tech_stack: str) -> str:
    """Classify the project's stack into a family we can scaffold for."""
    t = (tech_stack or "").lower()
    if any(k in t for k in ("python", "fastapi", "django", "flask")):
        return "python"
    if any(k in t for k in ("node", "typescript", "javascript", "react", "nest", "express", "next", "ts")):
        return "node"
    if any(k in t for k in ("java", "spring", "kotlin", "maven", "gradle")):
        return "java"
    return "generic"


def jira_project_key(name: str) -> str:
    """A stable 2–6 char uppercase key derived from the project name — the same
    algorithm the requirements prompt used to ask the model to apply."""
    words = re.findall(r"[A-Za-z0-9]+", name or "")
    if not words:
        return "PROJ"
    if len(words) == 1:
        key = words[0][:4].upper()
    else:
        key = "".join(w[0] for w in words).upper()
    key = re.sub(r"[^A-Z0-9]", "", key)[:6]
    return key if len(key) >= 2 else (key + "PRJ")[:4]


_EDITORCONFIG = """root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
indent_style = space
indent_size = 2
trim_trailing_whitespace = true
"""

_ESLINTRC = """{
  "root": true,
  "extends": ["eslint:recommended"],
  "env": { "node": true, "es2022": true },
  "parserOptions": { "ecmaVersion": 2022, "sourceType": "module" }
}
"""

_PRETTIERRC = """{
  "singleQuote": true,
  "printWidth": 100,
  "trailingComma": "all"
}
"""

_CHECKSTYLE = """<?xml version="1.0"?>
<!DOCTYPE module PUBLIC
  "-//Checkstyle//DTD Checkstyle Configuration 1.3//EN"
  "https://checkstyle.org/dtds/configuration_1_3.dtd">
<module name="Checker">
  <module name="TreeWalker">
    <module name="UnusedImports"/>
    <module name="AvoidStarImport"/>
    <module name="EmptyBlock"/>
    <module name="NeedBraces"/>
  </module>
</module>
"""


def quality_gate_files(tech_stack: str, coverage_min: int, lint_required: bool) -> list[dict]:
    """The coverage + linter/formatter config for the stack, with the coverage
    threshold pinned to `coverage_min`. Returned as {path, content} dicts to merge
    into the implementation stage's file set. Never raises."""
    stack = detect_stack(tech_stack)
    files: list[dict] = [{"path": ".editorconfig", "content": _EDITORCONFIG}]
    if stack == "python":
        files.append({"path": "pytest.ini", "content":
            "[pytest]\n"
            f"addopts = --cov=. --cov-report=term-missing --cov-fail-under={coverage_min}\n"
            "testpaths = tests\n"})
        if lint_required:
            files.append({"path": "ruff.toml", "content":
                "line-length = 100\n\n[lint]\nselect = [\"E\", \"F\", \"I\", \"B\", \"UP\", \"S\"]\n"})
    elif stack == "node":
        files.append({"path": "jest.config.cjs", "content":
            "module.exports = {\n"
            "  testEnvironment: 'node',\n"
            "  collectCoverage: true,\n"
            "  coverageThreshold: {\n"
            f"    global: {{ branches: {coverage_min}, functions: {coverage_min}, "
            f"lines: {coverage_min}, statements: {coverage_min} }},\n"
            "  },\n"
            "};\n"})
        if lint_required:
            files.append({"path": ".eslintrc.json", "content": _ESLINTRC})
            files.append({"path": ".prettierrc.json", "content": _PRETTIERRC})
    elif stack == "java":
        # Coverage is enforced in the build manifest (JaCoCo rule); ship a
        # Checkstyle baseline for the lint half of the gate.
        if lint_required:
            files.append({"path": "checkstyle.xml", "content": _CHECKSTYLE})
    return files
