"""Deterministic syntactic validation of generated content.

The validation agent (see phase_agents._validate_output) uses these free, offline
checks to catch the concrete syntax errors that used to slip through to the user —
malformed diagrams (the sequence-diagram `;` bug), unbalanced PlantUML, invalid
YAML/JSON payloads embedded as strings — and feeds them as precise rework
instructions to the phase agent. This is the "does it parse?" half of validation;
the LLM intent-check is the "does it mean the right thing?" half.

Each validator returns a list of human-readable problem strings (empty = clean).
`syntactic_issues()` walks a generated pydantic model's dict and dispatches the
right validator per field by name, so new structured fields are covered
automatically.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

import yaml

# ---------------------------------------------------------------- helpers


def _strip_fence(text: str, *langs: str) -> str:
    """Peel a leading/trailing ``` fence (optionally language-tagged) so a
    fenced payload validates as its raw content."""
    m = re.search(r"```(?:" + "|".join(langs) + r")?\s*\n?([\s\S]*?)```", text, re.IGNORECASE)
    return (m.group(1) if m else text).strip()


def _balanced(text: str, open_c: str, close_c: str) -> bool:
    return text.count(open_c) == text.count(close_c)


# ---------------------------------------------------------------- validators

_MERMAID_HEADER = re.compile(
    r"^(?:flowchart|graph|sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|erDiagram|journey|"
    r"gantt|pie|mindmap|timeline|quadrantChart|gitGraph|C4Context|C4Container|C4Component|"
    r"C4Dynamic|requirementDiagram|block-beta|xychart-beta|sankey-beta)\b"
)


def validate_mermaid(text: str) -> list[str]:
    src = _strip_fence(text, "mermaid", "mmd")
    if not src:
        return ["mermaid diagram is empty"]
    lines = [ln.rstrip() for ln in src.splitlines() if ln.strip()]
    header = next((ln.strip() for ln in lines if _MERMAID_HEADER.match(ln.strip())), None)
    issues: list[str] = []
    if header is None:
        return [
            "mermaid diagram has no recognised diagram type header "
            "(e.g. 'flowchart LR', 'sequenceDiagram', 'classDiagram')"
        ]
    if not _balanced(src, "[", "]"):
        issues.append("mermaid: unbalanced square brackets [ ]")
    if not _balanced(src, "(", ")"):
        issues.append("mermaid: unbalanced parentheses ( )")
    if not _balanced(src, "{", "}"):
        issues.append("mermaid: unbalanced braces { }")
    # In a sequenceDiagram a ';' inside a message label breaks the parser — the
    # exact failure that surfaced in the viewer. Flag any ';' on a message line.
    if header.startswith("sequenceDiagram"):
        for ln in lines:
            body = ln.strip()
            if ("->>" in body or "-->>" in body or "->" in body) and ";" in body:
                issues.append(f"mermaid sequence message contains ';' (breaks the parser): {body[:60]}")
                break
    return issues


def validate_plantuml(text: str) -> list[str]:
    src = _strip_fence(text, "plantuml", "puml")
    if not src:
        return ["PlantUML diagram is empty"]
    has_start = "@startuml" in src.lower() or "@startmindmap" in src.lower() or "@startgantt" in src.lower()
    has_end = "@enduml" in src.lower() or "@endmindmap" in src.lower() or "@endgantt" in src.lower()
    issues: list[str] = []
    if not has_start:
        issues.append("PlantUML is missing an @startuml directive")
    if not has_end:
        issues.append("PlantUML is missing an @enduml directive")
    return issues


def validate_yaml(text: str) -> list[str]:
    src = _strip_fence(text, "yaml", "yml")
    if not src.strip():
        return ["YAML content is empty"]
    try:
        yaml.safe_load(src)
    except yaml.YAMLError as err:
        detail = str(getattr(err, "problem", None) or err).splitlines()[0]
        return [f"invalid YAML: {detail[:160]}"]
    return []


def validate_json_str(text: str) -> list[str]:
    src = _strip_fence(text, "json")
    if not src.strip():
        return ["JSON content is empty"]
    try:
        json.loads(src)
    except json.JSONDecodeError as err:
        return [f"invalid JSON: {err.msg} at line {err.lineno} col {err.colno}"]
    return []


def validate_dbml(text: str) -> list[str]:
    src = _strip_fence(text, "dbml")
    if not src.strip():
        return ["DBML schema is empty"]
    issues: list[str] = []
    if not re.search(r"\bTable\s+\w", src, re.IGNORECASE):
        issues.append("DBML defines no Table")
    if not _balanced(src, "{", "}"):
        issues.append("DBML: unbalanced braces { }")
    return issues


# ---------------------------------------------------------------- dispatch

# (name-pattern, label, validator). First match on a string field wins. Ordered
# so more specific names (…Json, …Yaml) match before generic ones.
_FIELD_RULES: list[tuple[re.Pattern[str], str, object]] = [
    (re.compile(r"mermaid", re.I), "mermaid diagram", validate_mermaid),
    (re.compile(r"plantuml|puml", re.I), "PlantUML diagram", validate_plantuml),
    (re.compile(r"json$", re.I), "JSON payload", validate_json_str),
    (re.compile(r"yaml$|yml$", re.I), "YAML document", validate_yaml),
    (re.compile(r"dbml", re.I), "DBML schema", validate_dbml),
]


def _walk(node: object, key: str, out: list[tuple[str, str]]) -> None:
    if isinstance(node, str):
        for pattern, label, validator in _FIELD_RULES:
            if pattern.search(key):
                for problem in validator(node):  # type: ignore[operator]
                    out.append((f"{key} ({label})", problem))
                break
    elif isinstance(node, dict):
        for k, v in node.items():
            _walk(v, str(k), out)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _walk(item, key, out)  # keep the field name for list items (e.g. plantumlDiagrams[])


def syntactic_issues(data: dict) -> list[tuple[str, str]]:
    """Walk a generated model dict and return (field, problem) for every string
    field whose name identifies a structured format that failed to parse."""
    out: list[tuple[str, str]] = []
    _walk(data, "", out)
    return out


def format_issues(issues: Iterable[tuple[str, str]]) -> str:
    return "\n".join(f"- {field}: {problem}" for field, problem in issues)


# ---------------------------------------------------------------- deterministic auto-fix
# Best-effort, semantics-preserving repairs for the concrete errors validate_*
# detects. Anything they can't safely fix (missing header, unbalanced brackets)
# is left for the LLM repair pass — these never guess at meaning.


def autofix_mermaid(text: str) -> str:
    """Strip code fences + any preamble before the diagram header, and fix the
    sequence-message ';' bug (it breaks the parser) by using ',' in the label."""
    src = _strip_fence(text, "mermaid", "mmd")
    lines = src.splitlines()
    start = next((i for i, ln in enumerate(lines) if _MERMAID_HEADER.match(ln.strip())), 0)
    lines = lines[start:]
    header = lines[0].strip() if lines else ""
    if header.startswith("sequenceDiagram"):
        fixed: list[str] = []
        for ln in lines:
            if ("->>" in ln or "-->>" in ln or "->" in ln) and ";" in ln:
                if ":" in ln:
                    pre, _, msg = ln.partition(":")
                    ln = pre + ":" + msg.replace(";", ",")
                else:
                    ln = ln.replace(";", ",")
            fixed.append(ln)
        lines = fixed
    return "\n".join(lines).strip()


def autofix_plantuml(text: str) -> str:
    """Strip fences and add the @startuml/@enduml directives if missing."""
    src = _strip_fence(text, "plantuml", "puml")
    low = src.lower()
    if not any(d in low for d in ("@startuml", "@startmindmap", "@startgantt")):
        src = "@startuml\n" + src
    low = src.lower()
    if not any(d in low for d in ("@enduml", "@endmindmap", "@endgantt")):
        src = src.rstrip() + "\n@enduml"
    return src.strip()


def repair_diagram(content: str, kind: str) -> tuple[str, list[str]]:
    """Deterministically repair a diagram. Returns (fixed_content, remaining_issues);
    an empty issues list means the fix fully resolved it (no LLM needed). `kind` is
    'mermaid' or 'plantuml'."""
    if kind == "mermaid":
        fixed = autofix_mermaid(content)
        return fixed, validate_mermaid(fixed)
    if kind == "plantuml":
        fixed = autofix_plantuml(content)
        return fixed, validate_plantuml(fixed)
    return content, []
