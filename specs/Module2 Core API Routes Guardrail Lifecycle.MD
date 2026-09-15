Module 2: Core API Routes & Guardrail Lifecycle

1. Main Chat Interface Entrypoint

Route: POST /api/chat
Lifecycle implementation:

Input Guardrail: Validate incoming request against enterprise policies (no PII, no prompt injection).

Session Loading: Fetch current AgentState from Redis (fallback to Postgres).

Graph Invocation: Trigger the LangGraph pipeline (defined in Module 3) by passing the user input and current context.

Output Guardrail: Sanitize LLM outputs (e.g., strip raw secrets, ensure schema compliance).

Postgres Persistence: Save the updated AgentState.context and new artifacts to Aurora RDS.

Audit Log: Dispatch async event to EventBridge/SQS to write the interaction to S3.

Response: Return the AI response and updated Gate Status to the UI.

2. Gate Controller API (Human-in-the-loop)

Route: POST /api/gates/:projectId/phase/:phaseId/review

Payload: { decision: 'APPROVE' | 'AMEND', comments?: string }

Logic:

If APPROVE: Update DynamoDB PhaseState to APPROVED. Trigger PhaseRouter.advanceToPhase(phase+1, context). Accumulate context for the next phase.

If AMEND: Update state to AMEND_REQUESTED. Inject human comments back into the LangGraph state. Retrigger the Phase Agent to regenerate artifacts based on feedback.

3. Observability & Utility Routes

GET /api/kb/search: Semantic search against enterprise standards and allowlists (AWS service catalog).

GET /api/skills: Returns available MCP tool capabilities.

POST /api/webhooks/github: Receives GitHub Actions status updates to drive the Build Recovery Loop (Module 6).