---
id: phase.system.grounding
version: 2
description: 'Grounding directive: treat approved upstream artifacts and project config as the single source of truth.'
---
GROUNDING — the approved upstream artifacts, the retrieved knowledge, and the project profile below are the SINGLE SOURCE OF TRUTH for this stage. Build directly on them; this run must stay end-to-end consistent, not a fresh start.

Rules:
- Continue the SAME solution: reuse the exact names, identifiers, components, interfaces, data entities and terminology defined upstream (e.g. epic/feature/story IDs, the HLD's components and container names, the LLD's interfaces and schemas). Do not rename, re-scope or silently drop anything defined earlier.
- Every element you produce must trace to something upstream — a requirement, an acceptance criterion, an approved design element — or be a justified, explicitly-labelled decision.
- Target the project's configured technology stack and integration targets from the project profile; never switch stack, framework or platform.
- Never contradict an approved artifact or an enterprise standard. If two sources conflict, prefer the more recently approved artifact and note the conflict.
- If a required input is missing, ambiguous or contradictory, state it as an explicit assumption or open question — do NOT invent facts to fill the gap.
