---
id: impact-analyst
name: Impact analyst
version: 1
category: reviewer
runtime: proposed
status: proposed
description: When an upstream stage changes, says which downstream artefacts are actually affected and what to change, instead of only marking
  stages outdated.
role: reason
prompts: []
entrypoint: (proposed) app/services/flow.py::mark_downstream_stale
---
## Notes (not sent to the model)

# Impact analyst

When an upstream stage changes, says which downstream artefacts are actually affected and what to change, instead of only marking stages outdated.

## When it runs
After an approved stage is regenerated or amended.

## Context it receives
The old and new versions' difference and the downstream artefacts' summaries.

## What it returns
A list of affected artefacts with the reason and a suggested amendment.

## If it fails
Falls back to the current stale flag.

## Why it is an independent agent
Needs a clean context of diffs, not the stages' working context.
