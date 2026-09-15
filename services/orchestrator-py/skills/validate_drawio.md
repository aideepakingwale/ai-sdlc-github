---
id: validate_drawio
name: Validate draw.io architecture
description: Structurally and qualitatively check a draw.io (.drawio) architecture diagram (deterministic).
phase: 2
roles: [SA, TA]
tier: non_llm
executor: builtin
tools: []
input_hint: Paste the .drawio (mxGraph XML), or leave empty to check the latest architecture diagram.
needs_input: false
---

Validates a draw.io / diagrams.net diagram deterministically (no model call) and
returns actionable findings so the Solution/Technical Architect can self-correct
before the design gate — the same author → validate → fix loop the OpenAPI
contract gets from Spectral.

Checks include: well-formed mxGraph XML; the required base cells; unique cell ids;
every shape has a real geometry (positive size) and a label; every connector has
valid source/target endpoints; orthogonal edge routing; disconnected shapes; and
overlapping boxes. Runs against the pasted XML, or the project's latest editable
`DRAWIO` artifact when no input is given.
