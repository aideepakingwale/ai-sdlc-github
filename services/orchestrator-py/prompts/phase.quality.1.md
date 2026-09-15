---
id: phase.quality.1
version: 2
description: Phase 1 quality bar — an Agile backlog a product leader would sign off.
---
STAGE QUALITY BAR — Product definition (industry-standard Agile):
- Produce a real backlog HIERARCHY: Epic -> Feature -> User Story -> Sub-Task. Each epic carries a business case, a measurable success metric (metric, baseline, target) and a target quarter; each feature is a shippable capability with its own acceptance criteria; no epic has only one feature and no feature has only one story unless the scope is genuinely that small.
- Every story uses the structured form (as a / I want / so that), is INVEST-compliant and vertically sliced, is Fibonacci-estimated (1,2,3,5,8,13) with a one-line estimation rationale, and is split so none exceeds 8 points.
- Every story decomposes into technical SUB-TASKS estimated in hours (design, build, test, docs as appropriate) — enough that a team could start sprinting.
- Acceptance criteria are executable Gherkin (Given/When/Then) with concrete data, one behaviour per scenario, and at least one negative/edge scenario per story (validation failure, permission denied, idempotent replay, empty state).
- The PRD includes: problem statement with evidence; objectives and success metrics as a table (metric, baseline, target, measured-by); in-scope and EXPLICIT out-of-scope; personas; primary user journeys; functional requirements; non-functional targets with numbers (availability SLO, p95/p99 latency, throughput, security, accessibility); dependencies and assumptions; risks with mitigations and owners; a release/rollout and measurement plan; and open questions with named owners.
- Provide a Definition of Ready and a Definition of Done for the backlog.
- Set `jiraProjectKey` to a 2-6 uppercase-letter identifier derived from the product name (e.g. 'Agile Quality Demo Platform' -> AQDP); the epics/stories are keyed by it. If the reviewer's feedback asks to change the identifier, use exactly the key they specify.
