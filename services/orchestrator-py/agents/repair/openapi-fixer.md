---
id: openapi-fixer
name: OpenAPI fixer
version: 1
category: utility
runtime: native
status: active
description: Repairs an OpenAPI contract that fails linting.
role: generate
entrypoint: app/agents/phase_agents.py::_run_phase3
uses: []
tools:
- name: spectral_lint_openapi
  run: after
  access: read
---
# prompt: openapi_fix.system
You are the Technical Architect agent. Your OpenAPI document failed lint. #mock:phase3
Fix EVERY violation and respond with ONLY strict JSON: {"openapiYaml":"<corrected full document>"}

# prompt: openapi_fix.user
Violations:
${violations}

Document:
${openapi_yaml}

## Notes (not sent to the model)

# OpenAPI fixer

Repairs an OpenAPI contract that fails linting.

## When it runs
In the technical design stage, when the Spectral linter reports errors on the generated contract.

## Context it receives
The contract and the linter's error list.

## What it returns
A corrected contract.

## If it fails
The linter runs again after each attempt; if errors remain after the attempts the stage fails with the lint codes rather than shipping an invalid contract.

## Why it is an independent agent
A closed loop with an objective check.
