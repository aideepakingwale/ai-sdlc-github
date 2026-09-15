---
id: skill.system.wrapper
version: 2
description: Wrapper composing policy + skill instruction + stack + mock directive for every LLM skill.
variables:
- instruction
- mock_kind
- policy
- tech_stack
---
${policy}
${instruction} Target stack: ${tech_stack}. #mock:${mock_kind}
