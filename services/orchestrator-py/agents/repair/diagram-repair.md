---
id: diagram-repair
name: Diagram repair agent
version: 1
category: utility
runtime: native
status: active
description: Fixes or redraws a diagram whose source does not render.
role: generate
prompts:
- diagram_repair.fix.system
- diagram_repair.regenerate.system
- diagram_repair.user
entrypoint: app/api/project_routes.py::repair_diagram
---
# Diagram repair agent

Fixes or redraws a diagram whose source does not render.

## When it runs
When a reviewer asks to fix or regenerate a diagram that failed to render.

## Context it receives
The diagram source, the renderer's error, and the diagram type.

## What it returns
Corrected source in the same notation.

## If it fails
A deterministic syntax auto-fix runs first; if the model also fails the artefact is left untouched.

## Why it is an independent agent
A narrow, repeatable task with a hard success test (it renders or it does not).
