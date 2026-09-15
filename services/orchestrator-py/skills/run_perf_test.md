---
id: run_perf_test
name: Run performance test (k6)
description: Execute the generated k6 load script (non-LLM).
phase: 4
roles: [QA]
tier: non_llm
executor: mcp_run
tools: [k6_run_test]
artifact_type: K6_SCRIPT
mcp_tool: k6_run_test
mcp_arg: script
needs_input: false
---

Fetches the project's latest K6_SCRIPT artifact and runs it through the k6 engine. Reports rps,
p95/p99 latency, error rate and whether thresholds passed.
