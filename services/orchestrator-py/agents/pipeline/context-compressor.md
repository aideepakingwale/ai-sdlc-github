---
id: context-compressor
name: Context compressor
version: 1
category: utility
runtime: native
status: active
description: Condenses an approved upstream artefact that does not fit the token budget, keeping decisions, identifiers and numbers.
role: light
prompts:
- context.compression.system
entrypoint: app/services/context.py::build_context_block
---
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
