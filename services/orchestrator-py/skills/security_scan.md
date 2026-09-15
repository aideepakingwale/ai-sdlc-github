---
id: security_scan
name: Security scan (Trivy)
description: Re-scan the generated container image definition (non-LLM).
phase: 5
roles: [DEVOPS]
tier: non_llm
executor: mcp_run
tools: [trivy_scan_image]
artifact_type: DOCKERFILE
mcp_tool: trivy_scan_image
mcp_arg: dockerfile
mcp_extra_args: {imageTag: app:candidate}
needs_input: false
---

Fetches the project's latest DOCKERFILE artifact and scans the resulting image definition with
Trivy. Reports CVEs by severity and an overall PASS/FAIL.
