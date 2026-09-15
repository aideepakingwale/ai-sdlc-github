---
id: fact_check.user
version: 1
description: 'Fact-check node user turn: approved context plus the response under review.'
variables:
- context_summary
- response
---
Approved context:
${context_summary}

Response:
${response}
