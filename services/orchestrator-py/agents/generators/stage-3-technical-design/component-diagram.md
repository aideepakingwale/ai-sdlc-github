---
id: component-diagram
name: Component diagram agent
version: 1
category: generator
runtime: specialist
status: active
description: Describes the low-level component structure as a structured graph.
stage: 3
kind: diagram
role: generate
fields:
- componentDiagram
artifacts:
- COMPONENT_DIAGRAM
- DRAWIO
upstream: []
after:
- components
canon: true
stack: false
attachments: false
steering: false
---
# Role
You describe the low-level component structure for a diagram.

# What to produce
A structured graph: clusters (layers or packages), nodes (the components supplied) and edges (calls and data flow) with short labels.

# Rules
- One node per component supplied; edges follow the interfaces and dependencies given.
- No extra components, no unlabelled edges.

## Notes (not sent to the model)

### Reads
- Earlier-stage artefacts: none (works from the brief)
- Outputs of this stage it waits for: components
- Always: the reviewer's brief and any changes requested, project canon and team memory

### Output
JSON of the form `{"componentDiagram": ...}`:
- `componentDiagram`: `CloudArchitecture | None`

### Quality bar
- The source must parse and render; no ASCII art.
- Every component named in the context appears; nothing else is added.
- Labels are short and free of characters that break the syntax.

### Model role
`generate` — documents, diagrams and code; routed to the generation model.
