---
id: estimate_points
name: Estimate story points
description: Deterministic Fibonacci estimate from scope signals.
phase: 1
roles: [PO]
tier: non_llm
executor: builtin
input_hint: Describe the story/scope
---

Deterministic (non-LLM) sizing: scores scope keywords (integration, migration, security,
realtime, ...) and text length, then snaps to the Fibonacci scale 1/2/3/5/8/13/21.
