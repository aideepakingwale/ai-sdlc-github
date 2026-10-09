---
id: attachment-vision-reader
name: Attachment vision reader
version: 1
category: utility
runtime: native
status: active
description: Reads diagrams, slides and scanned pages in attached documents and describes them in text.
role: vision
entrypoint: app/services/attachment_extract.py
uses: []
---
# prompt: attachment.vision.user
You are extracting software-requirements context from an uploaded image (a screenshot, diagram, whiteboard photo, or scanned document).
1. Transcribe ALL text visible in the image VERBATIM, preserving structure (lists, tables, labels, field names, numbers, IDs).
2. Then, under a 'Description:' heading, describe any UI layout, diagram, flow, chart or data model shown. For a diagram list every component and EVERY connection as "A -> B (label)" with its direction, plus groupings and any legend; for a chart give its type, axes and the key values; for a table reproduce it as a Markdown table. End with what it implies for the software.
Be faithful to the image; do not invent details that are not visible.

# prompt: attachment.vision.figure.user
You are reading ONE figure from the uploaded document "${document}" (${location}). Caption / alt text: ${caption}. Nearby text: ${surrounding}.

Extract everything a software team needs from it, faithfully and without inventing anything that is not visible:
- TEXT: transcribe all visible text verbatim (labels, titles, legends, field names, numbers, IDs), keeping structure.
- DIAGRAM (architecture, flow, sequence, ER, state, network, org chart): list every component / actor / entity / step, then EVERY connection as "A -> B (label)" with its direction, grouping or swimlane boundaries, and any legend or numbered steps.
- CHART / GRAPH: type, axes and units, series, and the key values or trend.
- TABLE shown as an image: reproduce it as a Markdown table.
- UI MOCKUP / WIREFRAME: screens, fields, buttons, navigation and the user actions they imply.
- SCANNED PAGE: transcribe the page in reading order, keeping headings, lists and tables.
Finish with one or two sentences, headed "Meaning:", on what this implies for the software being specified. Be concise: no preamble, no restating these instructions.

## Notes (not sent to the model)

# Attachment vision reader

Reads diagrams, slides and scanned pages in attached documents and describes them in text.

## When it runs
When an uploaded document contains images or pages that cannot be read as text.

## Context it receives
One image at a time, plus the surrounding heading.

## What it returns
A structured text description of the figure (components, flows, labels, tables).

## If it fails
If no vision model is configured, falls back to OCR and says so; a mock run never pretends to have read an image.

## Why it is an independent agent
Needs a vision-capable model and one image, nothing else.
