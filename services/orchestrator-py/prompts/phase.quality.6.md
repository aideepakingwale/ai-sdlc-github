---
id: phase.quality.6
version: 3
description: Phase 6 quality bar — code that would pass a senior engineer's review.
---
STAGE QUALITY BAR — Implementation:
- Write production code, not a sketch: layered separation (API/application/domain/infrastructure), dependency injection, immutable domain types, and no business logic in controllers.
- Every public entry point validates input, enforces authorisation, and returns the documented error shape. Persistence uses parameterised queries or an ORM — never string concatenation. External calls carry timeouts, bounded retries with backoff, and circuit-breaking.
- Instrument: structured logs with correlation ids (never logging secrets or PII), the metrics named in the LLD, and trace spans around I/O.
- Tests accompany the code: arrange-act-assert, meaningful names stating the behaviour, the happy path plus failure and boundary cases, and deterministic fixtures — no sleeps, no network calls. Provide enough tests to satisfy the coverage gate (the coverage config is supplied by the platform, with the threshold already pinned — do not write it yourself).
- Lint-clean and consistently formatted: the linter/formatter config is supplied by the platform, so produce code that passes it with zero errors — no TODOs, commented-out code, dead parameters or unused imports. Do not regenerate the lint/format config files.
- PROJECT STRUCTURE: emit a real multi-file repository layout, never a single file and never one blob. Every file gets its full repository-relative path and the correct extension for its language, following the target stack's conventions — e.g. Java/Maven `src/main/java/<pkg>/X.java` + `src/test/java/<pkg>/XTest.java` + `pom.xml`; Node/TypeScript `src/<layer>/x.service.ts` + `src/<layer>/x.service.test.ts` + `package.json` + `tsconfig.json`; Python `<pkg>/x.py` + `tests/test_x.py` + `pyproject.toml`. Separate layers into their own folders, put tests in the stack's test location, and include the build manifest and a README.md.
- The PR body explains what changed and WHY, calls out risk and rollback, and lists the verification performed.
