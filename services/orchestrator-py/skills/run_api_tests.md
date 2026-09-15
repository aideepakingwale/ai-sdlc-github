---
id: run_api_tests
name: Run API tests (Postman)
description: Execute the generated Postman collection via newman (non-LLM).
phase: 4
roles: [QA]
tier: non_llm
executor: mcp_run
tools: [postman_run_collection]
artifact_type: POSTMAN_COLLECTION
mcp_tool: postman_run_collection
mcp_arg: collectionJson
needs_input: false
---

Fetches the project's latest POSTMAN_COLLECTION artifact and executes it through the newman
engine (`postman_run_collection`). Reports pass/fail per request.
