---
id: validate_diagram
name: Validate architecture diagram
description: Check Mermaid diagram syntax (non-LLM).
phase: 2
roles: [SA]
tier: non_llm
executor: builtin
needs_input: false
input_hint: Mermaid source (optional; uses HLD diagram if blank)
---

Deterministic Mermaid syntax check: verifies the source declares a known diagram type
(flowchart, sequenceDiagram, classDiagram, erDiagram, stateDiagram). Uses the project's HLD
diagram when no input is pasted.
