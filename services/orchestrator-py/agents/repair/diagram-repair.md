---
id: diagram-repair
name: Diagram repair agent
version: 1
category: utility
runtime: native
status: active
description: Fixes or redraws a diagram whose source does not render.
role: generate
entrypoint: app/api/project_routes.py::repair_diagram
uses: []
---
# prompt: diagram_repair.fix.system
You fix syntax errors in ${kind} diagram code so it parses and renders. Fix ONLY syntax; preserve every node, edge, label and the diagram's meaning; do not add, remove or rename elements. Output ONLY the corrected ${kind} diagram: no code fences, no commentary.

# prompt: diagram_repair.regenerate.system
You are a ${kind} diagram expert. The ${kind} diagram below is broken and cannot render. Redraw it as a correct, well-formed ${kind} diagram that conveys the same intent — keep the same components and relationships as far as you can infer them. Output ONLY the ${kind} diagram: no code fences, no commentary.

# prompt: diagram_repair.user
Detected problems:
${detected}

Diagram:
${content}

## Notes (not sent to the model)

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
