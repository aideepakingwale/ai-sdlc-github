---
id: draft_tests
name: Draft test cases
description: Draft Xray-style test cases from a story (frontier).
phase: 4
roles: [QA]
tier: frontier
executor: llm
mock_kind: chat
input_hint: Story/feature to test
---

You draft 3-5 Xray-style test cases for the story. Each test: a title, numbered steps (action +
expected result), covering the happy path, one edge case and one failure case.
