---
id: draft_story
name: Draft user story
description: Draft a user story with Gherkin acceptance criteria (frontier).
phase: 1
roles: [PO]
tier: frontier
executor: llm
mock_kind: phase1
input_hint: One-line capability
---

You draft a single user story from a one-line capability.

Output format:
1. Story: `As a <role>, I want <capability>, so that <benefit>`.
2. 2-4 Gherkin acceptance criteria (`Given / When / Then`).
3. A suggested story-point estimate on the Fibonacci scale.
