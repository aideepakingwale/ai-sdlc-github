---
id: fact_check.user
version: 1
description: 'Fact-check node user turn: approved context plus the response under review.'
variables:
- context_summary
- attached_digest
- response
---
Approved context:
${context_summary}

Material the requester attached (available to the agent that wrote the response):
${attached_digest}

Response:
${response}
