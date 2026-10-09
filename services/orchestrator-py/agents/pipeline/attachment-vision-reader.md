---
id: attachment-vision-reader
name: Attachment vision reader
version: 1
category: utility
runtime: native
status: active
description: Reads diagrams, slides and scanned pages in attached documents and describes them in text.
role: vision
prompts:
- attachment.vision.user
- attachment.vision.figure.user
entrypoint: app/services/attachment_extract.py
---
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
