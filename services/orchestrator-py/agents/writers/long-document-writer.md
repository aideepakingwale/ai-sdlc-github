---
id: long-document-writer
name: Long-document writer
version: 1
category: generator
runtime: native
status: active
description: Writes a Markdown document too long for one response in parts, from an outline.
role: generate
prompts: []
entrypoint: app/agents/phase_agents.py::_generate_markdown_in_parts
---
# Long-document writer

Writes a Markdown document too long for one response in parts, from an outline.

## When it runs
When a single-response write of a document is cut off.

## Context it receives
An outline first, then a few sections at a time with the outline and the tail of what was written.

## What it returns
The full document, joined.

## If it fails
Parts that fail are retried; the outline guarantees nothing is left out.

## Why it is an independent agent
Keeps very long documents within the output limit without losing sections.
