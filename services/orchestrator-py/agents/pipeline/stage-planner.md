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
entrypoint: app/services/chat.py::_compute_intelligent_plan
uses: []
---
# prompt: planner.system
You are the planner node of an SDLC agent pipeline. #mock:plan
Current stage: ${stage_seq} (${stage_name}).
Emit a short JSON execution plan: {"steps":[{"id":"...","tool":"llm|auto","description":"...","args":{}}]}

## Notes (not sent to the model)

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
