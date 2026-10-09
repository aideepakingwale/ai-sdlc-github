---
id: code-structure-planner
name: Code structure planner
version: 1
category: generator
runtime: native
status: active
description: Proposes the project's directory and file structure for the implementation stage, for approval before any code is written.
role: stage
prompts:
- code.structure.system
- code.structure.user
entrypoint: app/agents/code_generation.py
---
# Code structure planner

Proposes the project's directory and file structure for the implementation stage, for approval before any code is written.

## When it runs
At the start of the implementation stage (two-step code generation).

## Context it receives
The approved design artefacts, the stack and the brief.

## What it returns
A tree of directories and files, each with its purpose and layer.

## If it fails
The reviewer approves or edits the structure before implementation starts.

## Why it is an independent agent
Structure first keeps later per-file generation consistent.
