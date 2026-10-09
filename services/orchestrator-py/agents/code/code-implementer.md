---
id: code-implementer
name: Code implementer
version: 1
category: generator
runtime: native
status: active
description: Writes a batch of source files for the approved structure.
role: stage
prompts:
- code.implement.system
- code.implement.user
entrypoint: app/agents/code_generation.py
---
# Code implementer

Writes a batch of source files for the approved structure.

## When it runs
After the structure is approved, once per batch of files.

## Context it receives
The batch's file list and purposes, the approved design, the stack, and the interfaces of files already written.

## What it returns
File contents for the batch.

## If it fails
A failed batch is recorded and retried on its own; finished files are never lost.

## Why it is an independent agent
Batching keeps each call's context small and parallelisable.
