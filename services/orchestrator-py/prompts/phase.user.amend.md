---
id: phase.user.amend
version: 2
description: Wraps HITL reviewer feedback around the original request on regeneration.
variables:
- amend_comments
- user_input
---
This is a REVISION. A human gate reviewer rejected the previous output and requested the specific changes below. Treat their feedback as an INSTRUCTION with priority over your own defaults: apply every point exactly as asked (including concrete values, names, identifiers or wording they specify), keep everything else that was already approved, and begin your response by briefly listing what you changed in response to the feedback.

## Reviewer's requested changes (apply all)
${amend_comments}

## Original request (for context)
${user_input}
