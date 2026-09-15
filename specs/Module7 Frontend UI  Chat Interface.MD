Module 7: Frontend UI & Chat Interface

1. UI Architecture & Tech Stack

The frontend is a decoupled Single Page Application (SPA) or Server-Side Rendered (SSR) app.

Framework: Next.js (React) or Vite + React.

Styling: Tailwind CSS with Shadcn/UI for enterprise-grade, accessible components.

State Management: Zustand (for global session state, phase tracking, and gate status) and React Query / SWR (for API fetching and caching).

Real-time Communication: Server-Sent Events (SSE) or WebSockets to stream LLM reasoning and graph execution steps to the chat UI.

2. Core Layout & Views

A. Main Chat Interface (The Entrypoint)

Chat Window: The primary workspace. Displays user prompts and agent responses.

Renders markdown, code blocks, and inline Atlassian Document Format (ADF) previews.

Shows "Thinking..." states with sub-steps (e.g., "Calling Jira MCP...", "Linting OpenAPI spec...") streamed from the LangGraph executor_node.

Context Sidebar: Displays the active projectId, sessionId, current SDLC Phase (1 through 6), and a summary of the accumulated contextWindow (e.g., links to approved Epics, LLDs).

B. Human-in-the-Loop (HITL) Gate Dashboard

When an agent finishes a phase, the chat UI presents a "Gate Review" modal/panel tailored to the logged-in user's role (e.g., Product Owner, QA Lead).

Review Checklist: Renders the specific artifacts generated in the current phase with direct links to Confluence/Jira/GitHub.

Decision Controls:

Approve button -> Triggers POST /api/gates/:projectId/phase/:phaseId/review with { decision: 'APPROVE' }.

Amend button -> Opens a text area for the human to provide specific feedback (e.g., "Add a caching layer to the LLD"). Triggers the API with { decision: 'AMEND', comments: '...' }, looping back to the agent.

C. Observability & Skills View

Knowledge Base / Skills Drawer: A slide-out panel allowing users to see what MCP tools are currently active and query the enterprise KB (GET /api/skills, GET /api/kb/search).

Audit Trail / Traceability Matrix: A read-only view of the S3 Audit logs showing exactly which agent generated which artifact, the tokens used, and the human who approved it.

3. UI API Integration Lifecycle

Initiation: User types a prompt. UI calls POST /api/chat.

Streaming State: UI listens to the SSE stream. Updates the chat bubble with LangGraph state transitions (Planner -> Executor -> Synthesizer).

Artifact Generation: As MCP tools return success (e.g., { epic_key: "PROJ-1" }), the UI renders interactive badges/links to the newly created enterprise artifacts.

Gate Pause: The stream ends with a GATE_PENDING status. The UI locks the chat input and displays the HITL Gate Dashboard.

Progression: Upon human approval, the UI updates the global state to the next phase and unlocks the chat.