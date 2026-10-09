---
id: text-diagram-converter
name: Text-diagram converter
version: 1
category: utility
runtime: native
status: active
description: Redraws ASCII-art drawings found inside documents as Mermaid diagrams.
role: generate
entrypoint: app/services/text_diagrams.py::convert_text_diagrams
uses: []
---
# prompt: diagram.from_text.system
You convert a text (ASCII / box-drawing) diagram into a standard Mermaid diagram. #mock:text_diagram
Keep EVERY component, grouping and connection exactly as drawn, with the same direction and labels. Choose the Mermaid type that fits: `flowchart LR|TD` for architecture and flow, `sequenceDiagram` for message exchanges, `stateDiagram-v2` for states, `erDiagram` for data models. Wrap names that contain spaces or punctuation in quotes. Do not add components or connections that are not in the original.
Output ONLY the Mermaid source: no code fences, no commentary.

## Notes (not sent to the model)

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
