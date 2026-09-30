---
id: phase.system.stack
version: 1
description: Pins all designs/contracts/code to the project's technology stack.
variables:
- tech_stack
---
Target technology stack: ${tech_stack}. All designs, contracts and code MUST target this stack.

Infrastructure / cloud provider: INFER the deployment target (cloud provider, on-prem, or Kubernetes, and the deployment model) from the request, the technology stack and the provided context. Do NOT assume or default to any particular provider — in particular, do NOT default to AWS. If the target is stated or clearly implied, design for exactly that platform (services, IaC and diagrams must use that platform's equivalents — e.g. Terraform/Bicep/ARM/CDK/Helm as appropriate). If it is genuinely unspecified, choose the simplest option that fits the stack and constraints, and CLEARLY LABEL it as an assumption (call it out in an ADR and in the HLD) so a reviewer can correct it — never present an assumed platform as a given.
