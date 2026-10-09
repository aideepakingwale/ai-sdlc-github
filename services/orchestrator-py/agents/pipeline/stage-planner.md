---
id: stage-planner
name: Stage planner
version: 1
category: planner
runtime: native
status: active
description: 'Proposes the plan the reviewer sees before a stage runs: what will be produced, which tools and skills apply, and which steps to
  skip.'
role: plan
prompts:
- planner.system
entrypoint: app/services/chat.py::_compute_intelligent_plan
---
# Stage planner

Proposes the plan the reviewer sees before a stage runs: what will be produced, which tools and skills apply, and which steps to skip.

## When it runs
When the reviewer opens or edits the plan for a stage.

## Context it receives
The brief, the stage's outputs and tools, available skills and integrations, the traits of the project and the upstream artefacts.

## What it returns
A structured plan: artefacts to produce with reasons, recommended tools and skills, steps to skip and why.

## If it fails
Advisory only, single attempt. On failure the deterministic proposal is shown instead; nothing blocks.

## Why it is an independent agent
Planning is a different skill from writing; it runs on its own model route and never sees generated content.
