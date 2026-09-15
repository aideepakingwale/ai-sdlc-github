---
id: draft_dbml
name: Draft DBML table
description: Draft a DBML table from a description (local model).
phase: 3
roles: [TA]
tier: local
executor: llm
mock_kind: chat
input_hint: Entity + fields
---

You draft exactly one DBML table definition from the description. Use snake_case columns,
explicit types, a primary key, and `[not null]`/`[unique]` annotations where implied.
