---
id: context-compressor
name: Context compressor
version: 1
category: utility
runtime: native
status: active
description: Condenses an approved upstream artefact that does not fit the token budget, keeping decisions, identifiers and numbers.
role: light
entrypoint: app/services/context.py::build_context_block
uses: []
---
# prompt: context.compression.system
You compress SDLC artifacts for downstream context. #mock:compress
Summarise the artifact in <=150 words keeping every decision, constraint and identifier.

## Notes (not sent to the model)

# Context compressor

Condenses an approved upstream artefact that does not fit the token budget, keeping decisions, identifiers and numbers.

## When it runs
When the combined upstream context for a stage exceeds the token threshold.

## Context it receives
A single artefact body.

## What it returns
A shortened summary that keeps names, numbers and decisions.

## If it fails
The artefact is truncated at a section boundary instead.

## Why it is an independent agent
Runs once per artefact, in parallel, on the fast model.
