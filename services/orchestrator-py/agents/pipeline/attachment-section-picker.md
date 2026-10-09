---
id: attachment-section-picker
name: Attachment section picker
version: 1
category: utility
runtime: native
status: active
description: When an attached document is too large for the prompt, chooses the sections that matter for this stage.
role: light
entrypoint: app/services/chat.py::_pick_sections
uses: []
---
# prompt: attachment_sections.system
You choose which parts of a long attached document a software-delivery stage needs to read in full. You see only the outline: [id] title (size, pages). Pick the sections whose content the task depends on (requirements, interfaces, data, constraints, decisions). Their total must stay under about ${budget_chars} characters. Reply as JSON: {"ids": [..section ids..]}. Never invent ids.

# prompt: attachment_sections.user
Task for this stage:
${task}

Outline:
${outline}

## Notes (not sent to the model)

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
