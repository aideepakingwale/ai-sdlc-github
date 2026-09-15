---
id: phase.quality.4
version: 2
description: Phase 4 quality bar — a test strategy a QA manager would defend in audit.
---
STAGE QUALITY BAR — Test engineering:
- The strategy states: scope and test levels (unit/integration/contract/e2e/performance/security/accessibility); risk-based prioritisation tied to the risk register; entry and exit criteria per level; coverage targets with numbers; environment and test-data strategy (including PII handling and data refresh); defect severity definitions and triage SLAs; automation approach and CI integration; and a regression policy.
- Test cases are executable: preconditions, discrete numbered steps with concrete data, objectively verifiable expected results, priority, and the story they trace to. Cover boundary values, negative paths, authorisation failures and idempotent replays.
- The performance script encodes a real workload model: named scenarios, ramp profile, think times, data parameterisation, and thresholds asserting the NFR targets from the HLD — not arbitrary numbers.
- The RTM maps every requirement to at least one test and flags gaps explicitly.
- Also populate the STRUCTURED fields (rendered into the strategy): `testLevels` (the pyramid with scope, coverage target and tools per level), `riskAreas` (risk-based prioritisation), `entryCriteria`/`exitCriteria`, and `defectSlas` (triage + resolution per severity).
