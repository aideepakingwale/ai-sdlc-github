---
id: gen_dockerfile
name: Draft Dockerfile
description: Draft a Dockerfile for the tech stack (local model).
phase: 5
roles: [DEVOPS]
tier: local
executor: llm
mock_kind: chat
needs_input: false
input_hint: Service/runtime notes (optional)
---

You draft a production multi-stage Dockerfile for the project's tech stack: pinned base images,
non-root user, HEALTHCHECK, and a minimal runtime layer.
