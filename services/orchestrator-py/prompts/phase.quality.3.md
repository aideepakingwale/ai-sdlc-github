---
id: phase.quality.3
version: 1
description: Phase 3 quality bar — an LLD an engineer could implement without asking questions.
---
STAGE QUALITY BAR — Detailed design:
- The LLD covers: component decomposition with single-responsibility statements; class/module structure; sequence diagrams for the primary and one failure flow; transaction and concurrency model (isolation level, locking, idempotency keys); a complete error taxonomy mapping domain errors to HTTP status codes and client-facing messages; retry, timeout and circuit-breaker settings with values; caching strategy with TTLs and invalidation; pagination, filtering and sorting conventions; API versioning and deprecation policy; observability plan naming the metrics, structured log fields and trace spans; security controls at the boundary; and configuration/feature-flag inventory.
- The OpenAPI document is production-grade: operationIds, tagged operations, request/response schemas with examples, RFC 7807 problem+json error responses, pagination parameters, security schemes, and 4xx/5xx documented on every operation.
- The DBML carries primary/foreign keys, NOT NULL and UNIQUE constraints, indexes chosen for the documented access patterns, and column comments.
- The IaC stack is least-privilege: scoped IAM, encryption enabled, private networking, autoscaling policies, alarms, and resource tagging.
- Architecture diagram specs (`deploymentArchitecture` / `componentDiagram`): set `provider` to the CONFIRMED target cloud (aws | azure | gcp | onprem) — ONE cloud, never mixed — and use that cloud's own services or vendor-neutral keys (Azure: aks, appservice, containerapps, functions, cosmosdb, sql, servicebus, eventgrid, keyvault, frontdoor, apim, entra · AWS: ecs, fargate, lambda, dynamodb, rds, sqs, s3, cloudfront, cognito · GCP: gke, cloudrun, firestore, cloudsql, pubsub, bigquery). Users and external systems are ungrouped nodes (drawn outside the cloud boundary); group the rest by network boundary (network → zone → public/private subnet); label every edge with the protocol or interaction and mark asynchronous flows (publish/subscribe/event). Never name another cloud's service.
