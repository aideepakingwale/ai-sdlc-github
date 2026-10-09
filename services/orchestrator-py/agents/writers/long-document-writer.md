---
id: long-document-writer
name: Long-document writer
version: 1
category: generator
runtime: native
status: active
description: Writes a Markdown document too long for one response in parts, from an outline.
role: generate
entrypoint: app/agents/phase_agents.py::_generate_markdown_in_parts
uses: []
---
# prompt: long_document.outline.user
---
The `${field_name}` document is long, so it will be written in parts. First produce ONLY its outline as JSON {"sections": [{"title": "...", "covers": "one line: what it must contain"}]} - the document's real top-level sections in order (at most 20), following any governing format exactly.

# prompt: long_document.part.user
---
You are writing PART ${part} of ${parts} of the `${field_name}` document. Full outline:
${outline}

Write ONLY these sections, in full, as markdown: ${sections}. Use the outline's exact section titles as headings, keep numbering consistent with the outline, do not write any other section, and add no preamble, closing remarks or code fence around the document.${title_rule}

# prompt: long_document.title_rule.first
Start with the document's single '# ' title heading.

# prompt: long_document.title_rule.rest
Do NOT repeat the document title.

## Notes (not sent to the model)

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
