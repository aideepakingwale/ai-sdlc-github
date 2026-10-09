---
id: clarifier
name: Clarification agent
version: 1
category: planner
runtime: native
status: active
description: Checks the brief before generation and asks the few questions whose answers would change the output.
role: light
prompts:
- clarify.system
- clarify.user
- policy.clarification
entrypoint: app/services/chat.py::_model_clarification_questions
---
# Clarification agent

Checks the brief before generation and asks the few questions whose answers would change the output.

## When it runs
When a stage is triggered and has no confirmed clarifications yet.

## Context it receives
The brief, the stage's purpose and outputs, a digest of the approved upstream artefacts, and what the project already decided (stack, traits).

## What it returns
A list of at most a handful of questions, each with options, a rationale and whether a document would answer it better. An empty list means proceed.

## If it fails
If the model fails or returns invalid JSON the stage proceeds without questions (a deterministic stack question is still asked when the stack is undecided).

## Why it is an independent agent
Its job is judgement about missing information, which needs none of the generation context; running it on the fast model keeps it cheap and quick.
