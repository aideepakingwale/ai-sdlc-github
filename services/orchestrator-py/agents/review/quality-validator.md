---
id: quality-validator
name: Quality validator
version: 1
category: reviewer
runtime: native
status: active
description: Scores the generated output against the stage's quality bar and the brief, and names the artefacts that need rework.
role: light
prompts:
- validate.system
- validate.user
- phase.quality.1
- phase.quality.2
- phase.quality.3
- phase.quality.4
- phase.quality.5
- phase.quality.6
entrypoint: app/agents/phase_agents.py::_validate_output
---
# Quality validator

Scores the generated output against the stage's quality bar and the brief, and names the artefacts that need rework.

## When it runs
After a stage generates, before it goes to review.

## Context it receives
The stage's quality bar, the brief, the changes requested, and a digest of the output (not the full text), plus deterministic syntax checks.

## What it returns
A verdict with a score, issues by artefact and area, and the fields to regenerate.

## If it fails
A failed validation never blocks the stage; the output goes to review with the checks that did run. Rework regenerates only the flagged artefacts, on the reasoning model.

## Why it is an independent agent
An independent reviewer must not share the writer's context or it inherits its blind spots.
