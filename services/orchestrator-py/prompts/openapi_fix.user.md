---
id: openapi_fix.user
version: 1
description: 'OpenAPI fix user turn: violation list plus the failing document.'
variables:
- openapi_yaml
- violations
---
Violations:
${violations}

Document:
${openapi_yaml}
