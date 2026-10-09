---
id: fact-checker
name: Fact checker
version: 1
category: reviewer
runtime: native
status: active
description: Checks the stage's summary response against the approved context, the attached documents and the non-functional requirements.
role: light
prompts:
- fact_check.system
- fact_check.user
entrypoint: app/graph/pipeline.py::fact_check_node
---
# Fact checker

Checks that claims in the output are supported by the context it was given.

## When it runs
After generation, as a node in the stage graph.

## Context it receives
The stage's response (first 6,000 characters), a one-line summary of each approved artefact, and a digest of attached documents.

## What it returns
A pass flag and a list of issues.

## If it fails
Advisory: the issues are appended to the response as a visible caveat and never fail the stage.

## Why it is an independent agent
Independence from the writer is the point.
