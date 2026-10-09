---
id: phase.system.stack_layers
version: 1
description: Any stage other than the Technical Architect when some layers of the technology stack are decided (by the team or identified in the project's documents).
variables:
- layers
- undecided
---
Technology stack, by layer:
${layers}
${undecided}
Layers marked [pinned by the team] are decisions: designs, contracts and code MUST use them. Layers marked [identified in the project's documents] come from earlier stages: follow them unless the design gives a stated reason not to, and say so. Do not introduce a different technology for a layer that is listed.
