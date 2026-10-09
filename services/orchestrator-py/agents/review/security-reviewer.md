---
id: security-reviewer
name: Security reviewer
version: 1
category: reviewer
runtime: native
status: active
description: Reviews the stage's artefacts for security weaknesses and rates the overall risk; critical findings can block approval.
role: reason
prompts: []
entrypoint: app/services/security_gate.py::run_review
---
# Security reviewer

Reviews the stage's artefacts for security weaknesses and rates the overall risk; critical findings can block approval.

## When it runs
After generation for the stages configured in SECURITY_GATE_TEMPLATES (design, technical design, CI/CD, implementation).

## Context it receives
The stage's artefacts, the technology stack and a focus area (the skill 'security_review').

## What it returns
A risk rating, findings with severity, area, evidence and fix, gaps and next steps; each becomes a quality feedback item.

## If it fails
A failed review leaves the stage reviewable and says the review did not run.

## Why it is an independent agent
Security judgement needs the reasoning model and a clean context; it must not be the model that wrote the design.
