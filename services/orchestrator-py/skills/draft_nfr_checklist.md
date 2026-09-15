---
id: draft_nfr_checklist
name: Draft NFR checklist
description: Draft a non-functional requirements checklist for a feature (local model).
phase: 2
roles: [SA]
tier: local
executor: llm
mock_kind: chat
input_hint: Feature or component to assess
---

You draft a non-functional requirements checklist for the given feature. Cover: performance
(latency/throughput targets), availability, security (authn/authz, data classification),
observability, scalability and cost. One measurable line per item, as a markdown checklist.
