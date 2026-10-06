---
id: code.structure.user
version: 1
description: 'Code generation step 1 user turn: the request, approved upstream design and any reviewer feedback on a previous proposal.'
variables:
- user_input
- context_block
- feedback_block
---
## Request
${user_input}

## Approved design and context from earlier stages
${context_block}

${feedback_block}

Propose the repository structure now (JSON only).
