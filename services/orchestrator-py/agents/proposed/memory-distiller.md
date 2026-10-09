---
id: memory-distiller
name: Memory distiller
version: 1
category: planner
runtime: proposed
status: proposed
description: Turns a raw clarification answer or change request into a clean, reusable memory statement and decides whether it is worth remembering.
role: light
prompts: []
entrypoint: (proposed) app/services/memory.py
---
# Memory distiller

Turns a raw clarification answer or change request into a clean, reusable memory statement and decides whether it is worth remembering.

## When it runs
When a memory suggestion is created.

## Context it receives
One answer or change request and the stage it came from.

## What it returns
A short title and statement, or 'not worth remembering'.

## If it fails
Falls back to today's rule-based suggestion.

## Why it is an independent agent
Today suggestions copy the reviewer's words; a distiller would make them reusable and cut noise.
