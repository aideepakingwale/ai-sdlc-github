---
id: summarise
name: Summarise input
description: Condense notes/requirements into a short summary (local model).
roles: [PO, SA, TA, QA, DEVOPS, DEV]
tier: local
executor: llm
mock_kind: chat
input_hint: Text to summarise
---

You summarise SDLC notes in <=120 words. Keep every decision, constraint, owner and identifier;
drop pleasantries and repetition. Output plain markdown.
