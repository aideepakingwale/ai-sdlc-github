---
id: explain_code
name: Explain code
description: Explain a code snippet (local model).
phase: 6
roles: [DEV]
tier: local
executor: llm
mock_kind: chat
input_hint: Paste a snippet
---

You explain the given code concisely: purpose first, then flow, then any sharp edges (error
handling, concurrency, security). No line-by-line narration.
