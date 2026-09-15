---
id: run_ui_tests
name: Run UI tests (Playwright)
description: Execute the generated Playwright spec headlessly (non-LLM).
phase: 4
roles: [QA]
tier: non_llm
executor: mcp_run
tools: [playwright_run_tests]
artifact_type: PLAYWRIGHT_SPEC
mcp_tool: playwright_run_tests
mcp_arg: specTs
needs_input: false
---

Fetches the project's latest PLAYWRIGHT_SPEC artifact and executes it headlessly through
`playwright_run_tests`. Reports totals and failures.
