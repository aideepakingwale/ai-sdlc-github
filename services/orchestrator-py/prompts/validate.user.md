---
id: validate.user
version: 1
description: 'Validation agent user turn: the stage, the user intent + reviewer feedback, a digest of the generated output, and any pre-detected syntax errors.'
variables:
- stage_name
- quality_bar
- user_intent
- amend_comments
- output_digest
- syntax_errors
---
## Stage under validation
${stage_name}

## What a senior reviewer requires for this stage
${quality_bar}

## The user's request (intent)
${user_intent}

## Reviewer's requested changes (must be honoured exactly; empty if none)
${amend_comments}

## Digest of the generated output
${output_digest}

## Pre-detected syntax errors (deterministic; empty if none)
${syntax_errors}

Judge the output against the intent and the reviewer's requested changes, then
respond with the strict JSON verdict.
