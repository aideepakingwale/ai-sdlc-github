Module 3: LangGraph Orchestration & LLM Router

1. AgentState Definition

Define the AgentState TypedDict to thread data through the pipeline:

interface AgentState {
  projectId: string;
  currentPhase: number;
  userInput: string;
  contextWindow: any[]; // Accumulates approved artifacts (Epics -> HLD -> LLD -> Tests)
  plan: PlanStep[]; // Populated by planner_node
  currentStepIndex: number;
  stepOutputs: Record<string, any>;
  errors: string[];
  finalResponse: string;
}


2. LLMRouter with Circuit Breaker (corelogic)

Implement LLMRouter.ts to manage Groq, Gemini, and Grok.

Routing logic:

Intent = recommendation or architecture -> Groq / Grok

Intent = generation or standard -> Groq / Gemini Flash

Circuit Breaker Pattern:

On API call, wrap in try/catch.

If status === 429: Implement exponential backoff retry (up to 3 times).

If status === 401 | 403 | 402 (billing): Mark provider as DISABLED_FOR_LIFETIME in memory. Failover immediately to the next provider in the chain.

3. LangGraph Nodes

Implement the graph pipeline using @langchain/langgraph:

planner_node: LLM emits a JSON execution plan.

Heuristic Fast-Path: If input length < 50 chars and intent is "status", skip LLM and return static plan.

executor_node: Resolves $inputs.* and $steps.* references from previous step outputs. Dispatches to MCP Tools (Module 4).

synthesizer_node: Formats the tool outputs based on the specific SDLC phase rendering path.

response_formatting_agent: Applies Atlassian Document Format (ADF) or Markdown styling.

fact_check_agent: Validates outputs against NFRs and constraints in AgentState.contextWindow.

Context Compression Layer: Runs before LLM calls. If total tokens > threshold, use an LLM call to summarize older artifacts (e.g., summarize Epics while keeping LLD specs exact) to reduce token costs without losing intent.