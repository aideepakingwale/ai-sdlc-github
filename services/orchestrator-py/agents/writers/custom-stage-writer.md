---
id: custom-stage-writer
name: Custom stage writer
version: 1
category: generator
runtime: native
status: active
description: Writes the deliverables of a custom stage (for example Deployment & Release, Maintenance) defined by the workflow designer.
role: stage
prompts:
- phase.custom.system
- phase.custom.user
entrypoint: app/agents/phase_agents.py::_run_custom
---
# Custom stage writer

Writes the deliverables of a custom stage (for example Deployment & Release, Maintenance) defined by the workflow designer.

## When it runs
When a stage with template 7 runs.

## Context it receives
The stage's persona, outputs and tools, the brief and the approved upstream artefacts.

## What it returns
One document per deliverable the stage declares.

## If it fails
Per-deliverable failures are reported; the rest are kept.

## Why it is an independent agent
Candidate for splitting into per-deliverable specialists (see docs/specialist-agents.md).
