Module 1: Architecture, Infrastructure & Database Schemas

1. Tech Stack & Container Architecture (HLD Fig 1.2 & 1.3)

The system is built on Node.js/TypeScript using ECS Fargate behind an ALB.
Docker Compose Configuration:
Must include the following services:

orchestrator-hub: Node.js/TS (1vCPU, 2GB). Handles phase routing, context management, prompt engine, gate controller.

ai-client-service: Node.js/TS (2vCPU, 4GB). Manages connections to Groq, Gemini, Grok, Amazon Q, MS Copilot.

tool-connector-service: Node.js/TS (1vCPU, 2GB). MCP servers for Jira, Confluence, GitHub.

postgres: Local Aurora RDS mock for Orchestration state.

redis: Local ElastiCache mock for session cache and rate limiting.

dynamodb-local: Local DynamoDB mock for Phase/Gate state and Build Loop counters.

localstack: For mocking S3 (Audit Logs), SQS/EventBridge (Event queue), Secrets Manager.

2. Database Schemas

Aurora RDS (PostgreSQL) - Orchestration & Context

Use Prisma or Drizzle ORM.

Project: id (UUID), name, status, created_at

Session: id, project_id, current_phase (1-6), context_window_summary (JSONB)

Artefact: id, project_id, phase_id, type (Epic, LLD, OpenAPI, etc.), content_url, version

DynamoDB - Transient State & Gates

Use AWS SDK v3 DynamoDB DocumentClient.

Table PhaseState:

PK: PROJECT#<project_id>, SK: PHASE#<phase_id>

Attributes: status (IN_PROGRESS, PENDING_REVIEW, APPROVED, AMEND_REQUESTED), reviewer_role

Table BuildRecoveryTracker:

PK: BUILD_RUN#<run_id>

Attributes: iteration_count (Number, max 5), failed_job_ids (List), last_root_cause (String)

Redis (ElastiCache)

Keys: session:<session_id> (TTL 24h)

Tool Cache: mcp_cache:<tool_hash> (TTL 24h - for deterministic code/testcase gen)

Rate Limits: ratelimit:<provider>:<timestamp>

3. S3 Audit Log (Parquet/Immutable)

Every AI generation event writes an immutable JSON/Parquet log to S3:
{ timestamp, phase, agent_role, prompt_tokens, completion_tokens, generated_artefact_hash, human_reviewer }