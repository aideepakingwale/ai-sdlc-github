---
id: kb_search
name: Search knowledge base
description: Semantic search over standards + approved artifacts.
roles: [PO, SA, TA, QA, DEVOPS, DEV]
tier: non_llm
executor: builtin
input_hint: What to search for
---

Runs the platform's deterministic RAG retrieval (feature-hash embeddings) over enterprise
standards, approved artifacts and any uploaded codebase. Returns the top matches with scores. No
LLM involved.
