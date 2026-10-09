---
id: trait-classifier
name: Project trait classifier
version: 1
category: planner
runtime: native
status: active
description: Decides which traits apply to the project (UI, API, database, messaging, compliance), so the plan and the artefacts skip what does
  not apply.
role: light
entrypoint: app/services/chat.py::resolve_project_traits
uses: []
---
# prompt: traits.system
You classify what kind of software project this is, so an SDLC pipeline only plans and generates artifacts that apply to it. For each characteristic answer present, absent or unknown, quote the evidence from the input, and give a confidence 0-1. Say `unknown` unless the input states or clearly implies it — NEVER guess, and absence of a mention is not absence of the trait. Judge the project as described, not the generic pipeline.

# prompt: traits.user
PROJECT: ${project_name}
TECH STACK: ${tech_stack}
USER INSTRUCTIONS: ${instructions}
UPSTREAM OUTPUTS / CONTEXT:
${context}

## Notes (not sent to the model)

# Project trait classifier

Decides which traits apply to the project (UI, API, database, messaging, compliance), so the plan and the artefacts skip what does not apply.

## When it runs
When a plan is built or a stage is triggered and the traits are not already fixed by the user.

## Context it receives
The brief and a short digest of upstream artefact titles.

## What it returns
A yes/no per trait with a confidence, merged with keyword rules and any manual override.

## If it fails
Falls back to keyword rules and stored values; a manual override always wins.

## Why it is an independent agent
A tiny classification problem that must be cheap and deterministic (temperature 0).
