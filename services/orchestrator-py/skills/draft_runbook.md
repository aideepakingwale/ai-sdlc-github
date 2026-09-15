---
id: draft_runbook
name: Draft operational runbook
description: Draft an incident/operations runbook for the service (frontier).
phase: 5
roles: [DEVOPS]
tier: frontier
executor: llm
mock_kind: chat
input_hint: Service or scenario (e.g. 'p99 latency spike')
---

You draft an operational runbook section for the given scenario with: symptoms, dashboards to
check (reference the generated Grafana dashboard), triage steps, mitigation, rollback procedure
and escalation path. Keep every step imperative and executable.
