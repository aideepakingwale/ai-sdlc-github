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
prompts: []
entrypoint: app/services/chat.py::resolve_project_traits
---
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
