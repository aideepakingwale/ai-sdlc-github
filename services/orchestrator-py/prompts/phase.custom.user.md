---
id: phase.custom.user
version: 1
description: 'Custom phase user turn: the request plus the approved upstream context.'
variables:
- user_input
- context_block
---
## Request
${user_input}

## Approved context from earlier stages
${context_block}

Produce this stage's Markdown deliverable now, addressing every expected output.
