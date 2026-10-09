---
id: attachment-section-picker
name: Attachment section picker
version: 1
category: utility
runtime: native
status: active
description: When an attached document is too large for the prompt, chooses the sections that matter for this stage.
role: light
prompts: []
entrypoint: app/services/chat.py::_pick_sections
---
# Attachment section picker

When an attached document is too large for the prompt, chooses the sections that matter for this stage.

## When it runs
When attached material exceeds the context budget for a stage.

## Context it receives
The outline of the document (section ids, titles, sizes, pages), not its text, plus the brief.

## What it returns
The section ids to include in full, kept within half of the document's character budget; the rest are condensed.

## If it fails
Any failure falls back to keyword fitting, so a document is never cut off after page one. Its prompt is still written inline in `chat.py`; moving it into the prompt library is open work.

## Why it is an independent agent
Selection needs only headings and the brief, not the document body.
