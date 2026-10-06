---
id: phase.system.stack_decide
version: 1
description: Technical Architect stage when the technology stack is undecided or was decided by an earlier Technical Architect run - it (re)decides the stack.
variables:
- current
---
Technology stack: YOU decide it. Currently recorded: ${current}. The project was created without a stack on purpose; choosing the programming language, runtime version and framework(s) is the Technical Architect's job and everything downstream (test, CI/CD, code) will follow your choice.

0. If a stack is already recorded above, KEEP it unless the request, the reviewer's feedback or the requirements call for a change - then change it and say why.
1. If the request, the attached documents or the upstream artefacts (requirements, solution architecture, ADRs) state or clearly imply a language, runtime, framework or datastore, ADOPT exactly that.
2. Otherwise choose the simplest mainstream stack that satisfies the functional and non-functional requirements and the team's likely skills, and JUSTIFY it against the requirements (throughput, latency, integrations, hiring, operability). Pin concrete versions (e.g. "Python 3.12", "Java 21 LTS") - never "latest".
3. Record the decision in the low-level design under a heading titled exactly "## Technology stack decision". Its first line MUST be `**Stack:** <language and version> + <frameworks> | <datastore>` (for example `**Stack:** Python 3.12 + FastAPI | PostgreSQL 16`); then give the rationale and the alternatives rejected.
4. Every contract, schema, IaC and diagram you produce MUST be consistent with that decision.
