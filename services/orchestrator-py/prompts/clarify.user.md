---
id: clarify.user
version: 1
description: User message for the ambiguity pre-check.
variables:
- request
- project_profile
- context_digest
---
Request for this stage:
${request}

Project profile:
${project_profile}

Available context (upstream artifacts / prior decisions):
${context_digest}

Decide whether clarification is required before generating, per your instructions.
