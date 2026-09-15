---
id: validate_pipeline
name: Validate CI pipeline
description: Check the CI workflow has the 7 mandated stages (non-LLM).
phase: 5
roles: [DEVOPS]
tier: non_llm
executor: builtin
needs_input: false
---

Deterministic check that the generated GitHub Actions workflow contains the 7 mandated stages:
checkout, lint, build, test, snyk, inspector, deploy.
