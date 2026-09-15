---
id: json_repair.user
version: 1
description: 'JSON repair nudge: ask the model to re-emit strict JSON after an invalid response.'
variables:
- issues
---
Your previous JSON was invalid: ${issues}. Respond again with ONLY corrected strict JSON.
