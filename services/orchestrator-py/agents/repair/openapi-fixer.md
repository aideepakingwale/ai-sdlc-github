---
id: openapi-fixer
name: OpenAPI fixer
version: 1
category: utility
runtime: native
status: active
description: Repairs an OpenAPI contract that fails linting.
role: generate
prompts:
- openapi_fix.system
- openapi_fix.user
entrypoint: app/agents/phase_agents.py::_run_phase3
---
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
