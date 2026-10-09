---
id: review-summariser
name: Review summariser
version: 1
category: reviewer
runtime: proposed
status: proposed
description: Writes the reviewer a short summary of what a stage produced and what changed since the last version, with open findings.
role: light
prompts: []
entrypoint: (proposed) app/api/project_routes.py
---
## Notes (not sent to the model)

# Review summariser

Writes the reviewer a short summary of what a stage produced and what changed since the last version, with open findings.

## When it runs
When a stage reaches review.

## Context it receives
The artefact versions, quality findings and the changes requested last time.

## What it returns
A half-page summary.

## If it fails
None needed; it is optional.

## Why it is an independent agent
Reviewers should not have to diff versions by hand.
