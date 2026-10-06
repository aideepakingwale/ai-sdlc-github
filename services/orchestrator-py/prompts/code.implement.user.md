---
id: code.implement.user
version: 1
description: 'Code generation step 2 user turn: the batch to write, the full approved file plan, and the upstream design.'
variables:
- user_input
- batch_block
- plan_block
- context_block
- files_marker
---
## Request
${user_input}

## Files to write now
${batch_block}

## The whole approved structure (for consistent imports and names)
${plan_block}

## Approved design and context from earlier stages
${context_block}

${files_marker}
Write the listed files now (JSON only).
