---
id: classify_failure
name: Classify build failure
description: Classify a CI failure log (local model).
phase: 6
roles: [DEV]
tier: local
executor: llm
mock_kind: failure_analysis
input_hint: Paste the failing log
---

You classify a CI failure log. Output: root-cause class (type-error | test-failure | dependency
| infrastructure | flake), the one-line root cause, and the most likely file(s).
