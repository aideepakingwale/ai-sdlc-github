---
id: text-diagram-converter
name: Text-diagram converter
version: 1
category: utility
runtime: native
status: active
description: Redraws ASCII-art drawings found inside documents as Mermaid diagrams.
role: generate
prompts:
- diagram.from_text.system
entrypoint: app/services/text_diagrams.py::convert_text_diagrams
---
# Text-diagram converter

Redraws ASCII-art drawings found inside documents as Mermaid diagrams.

## When it runs
When a narrative artefact is saved and contains a text drawing.

## Context it receives
Only the drawing and a line of surrounding text.

## What it returns
A Mermaid diagram that replaces the drawing in place.

## If it fails
The original text is kept if conversion fails.

## Why it is an independent agent
Sees one drawing at a time, never the document.
