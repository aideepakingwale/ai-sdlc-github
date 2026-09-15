---
id: lint_openapi
name: Lint OpenAPI
description: Run Spectral on the project's OpenAPI artifact (non-LLM).
phase: 3
roles: [TA]
tier: non_llm
executor: builtin
tools: [spectral_lint_openapi]
needs_input: false
---

Runs the Spectral-style OpenAPI linter (via the `spectral_lint_openapi` MCP tool) against the
project's latest OPENAPI artifact and reports PASS/FAIL with each violation.
