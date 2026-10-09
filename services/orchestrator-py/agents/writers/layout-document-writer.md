---
id: layout-document-writer
name: Layout document writer
version: 1
category: generator
runtime: native
status: active
description: Writes one artefact in the layout of an attached document the reviewer chose for it.
role: stage
prompts:
- artifact.layout.system
entrypoint: app/agents/phase_agents.py::_generate_layout_doc
---
# Layout document writer

Writes one artefact in the layout of an attached document the reviewer chose for it.

## When it runs
When the reviewer points an artefact at an attached file's structure.

## Context it receives
The reference layout (structure only), the brief, canon and memory, and the upstream context.

## What it returns
The artefact in that layout.

## If it fails
Falls back to the standard layout if the attachment cannot be read.

## Why it is an independent agent
A single artefact with a specific form.
