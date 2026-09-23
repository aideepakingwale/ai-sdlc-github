---
id: phase.system.quality_gate
version: 2
description: Configurable coverage + lint quality gate injected into the code, test and CI/CD stages.
---
## Quality gate (mandatory — the output must be able to pass it)
This stage is subject to an enforced quality gate. Produce work that passes it, and make the gate real, not aspirational:
- Unit-test coverage MUST be at least ${coverage_min}% of the code this stage produces. Write enough meaningful tests (happy, negative and boundary paths — not filler) to reach it, and include the coverage tool and its threshold in the build configuration so a run below ${coverage_min}% fails.
- ${lint_requirement}
- The platform provides the coverage and linter/formatter configuration files for the implementation stage DETERMINISTICALLY (with the threshold already pinned to ${coverage_min}%). Do NOT regenerate `.editorconfig`, the coverage config (e.g. `pytest.ini`, `jest.config.cjs`) or the lint config (e.g. `ruff.toml`, `.eslintrc.json`, `.prettierrc.json`) — spend your output on application code and tests instead, and simply assume those files exist. For a build manifest that must embed coverage (e.g. Maven/Gradle JaCoCo), configure it to fail under ${coverage_min}%.
- Where this stage defines CI/CD, the pipeline MUST run lint and coverage as BLOCKING gates that fail the build when lint reports errors or coverage is below ${coverage_min}%, before any packaging or deploy step.
State briefly how the gate is satisfied (the coverage target and the lint/static-analysis config used).
