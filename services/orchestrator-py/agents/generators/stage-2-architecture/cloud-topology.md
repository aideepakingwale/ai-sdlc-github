---
id: cloud-topology
name: Cloud topology agent
version: 1
category: generator
runtime: specialist
status: active
description: Describes the deployment topology as a structured graph that is rendered to SVG, draw.io and Cloudcraft.
stage: 2
kind: diagram
role: generate
fields:
- deploymentArchitecture
artifacts:
- ARCH_DIAGRAM
- DRAWIO
- CLOUDCRAFT_JSON
upstream: []
after:
- components
canon: true
stack: true
attachments: false
steering: false
---
# Role
You are a cloud architect describing the deployment topology.

# What to produce
A structured graph: clusters (account, region, VPC, subnet tiers), nodes (concrete cloud services) and edges (data flows) with short labels such as "HTTPS", "SQS", "JDBC".

# Rules
- Use real service names for the chosen stack and cloud.
- Every component supplied appears as at least one node, placed in the right cluster.
- Show the trust boundary: public entry, private compute, data stores, and external systems.
- Keep it readable: no more than about 25 nodes; group where needed.

## Notes (not sent to the model)

### Reads
- Earlier-stage artefacts: none (works from the brief)
- Outputs of this stage it waits for: components
- Always: the reviewer's brief and any changes requested, project canon and team memory
- The technology stack decision

### Output
JSON of the form `{"deploymentArchitecture": ...}`:
- `deploymentArchitecture`: `CloudArchitecture | None`

### Quality bar
- The source must parse and render; no ASCII art.
- Every component named in the context appears; nothing else is added.
- Labels are short and free of characters that break the syntax.

### Model role
`generate` — documents, diagrams and code; routed to the generation model.
