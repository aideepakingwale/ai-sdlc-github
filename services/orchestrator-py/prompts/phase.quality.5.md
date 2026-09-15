---
id: phase.quality.5
version: 2
description: Phase 5 quality bar — a pipeline and images a platform team would run in production.
---
STAGE QUALITY BAR — CI/CD and platform:
- The pipeline pins action versions, uses dependency caching, fails fast, runs jobs in parallel where independent, and enforces quality gates that BLOCK on failure: lint, unit and integration tests with coverage thresholds, SAST, dependency/SCA scan, container image scan, IaC scan, SBOM generation, and artifact signing.
- Deployments are environment-scoped with manual approval for production, use a progressive strategy (blue/green or canary) with automated health verification, and define an explicit rollback path. Secrets come from a secret manager via OIDC — never long-lived credentials in the workflow.
- Dockerfiles are hardened: multi-stage, minimal/pinned base image, no build tools in the runtime layer, non-root USER, read-only filesystem where possible, HEALTHCHECK, explicit EXPOSE, .dockerignore assumptions stated, and no secrets or credentials baked in.
- The dashboard defines the four golden signals plus business KPIs, with alert thresholds tied to the SLOs agreed in the HLD.
- Also populate the STRUCTURED fields (rendered into a CI/CD & operations design doc): `pipelineStages` (name, purpose, blocking, tools), `securityGates` (the blocking shift-left scans), `observabilitySlos` (target + alert threshold), and `rolloutStrategy` + `rollback`.
