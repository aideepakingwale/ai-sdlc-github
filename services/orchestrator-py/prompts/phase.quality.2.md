---
id: phase.quality.2
version: 1
description: Phase 2 quality bar — an HLD an architecture review board would approve.
---
STAGE QUALITY BAR — Solution architecture:
- The HLD covers, in order: executive summary; business context and drivers; scope and explicit non-goals; assumptions and constraints; C4 context and container views; component responsibilities; integration and interface catalogue (protocol, payload, sync/async, SLA); data architecture with classification and residency; security architecture (authN/authZ model, encryption in transit/at rest, secret management, STRIDE-style threat table with mitigations); quality attributes as measurable targets (availability SLO, p95/p99 latency, throughput, RPO/RTO, retention); capacity and cost estimate; deployment topology and environments; resilience and failure-mode analysis; disaster recovery; migration/cutover approach; risks with owners; and a traceability table mapping requirements to components.
- ADRs use the full form: context, decision drivers, options CONSIDERED WITH TRADE-OFFS, the decision, consequences (positive and negative), and status.
- The Structurizr DSL and Mermaid diagram must parse, use consistent element names with the narrative, and show trust boundaries and data stores.
