import { z } from 'zod';
import { ArtifactTypeSchema, PhaseStatusSchema } from './phases.js';

/**
 * A single approved (or in-flight) artifact carried in the context window.
 * `exact: true` artifacts are injected verbatim into prompts (e.g. OpenAPI, LLD);
 * `exact: false` artifacts may be replaced by their `summary` under token pressure
 * (context compression layer, Module 3).
 */
export const ContextArtifactSchema = z.object({
  phase: z.number().int().min(1).max(6),
  type: ArtifactTypeSchema,
  title: z.string(),
  summary: z.string(),
  exact: z.boolean().default(false),
  /** Full body; may be dropped by compression when exact=false. */
  content: z.string().optional(),
  /** External references (Jira key, Confluence URL, GitHub path). */
  ref: z
    .object({
      url: z.string().optional(),
      key: z.string().optional(),
      path: z.string().optional(),
    })
    .optional(),
});
export type ContextArtifact = z.infer<typeof ContextArtifactSchema>;

/** One step of the planner's JSON execution plan. */
export const PlanStepSchema = z.object({
  id: z.string(),
  /** MCP tool name, or 'llm' for a pure generation step. */
  tool: z.string(),
  description: z.string(),
  /** Args may contain `$inputs.*` / `$steps.<id>.<path>` references resolved by the executor. */
  args: z.record(z.unknown()).default({}),
});
export type PlanStep = z.infer<typeof PlanStepSchema>;

/** LangGraph channel state threaded through the pipeline (Module 3 §1, typed per). */
export const AgentStateSchema = z.object({
  projectId: z.string(),
  sessionId: z.string(),
  currentPhase: z.number().int().min(1).max(6),
  userInput: z.string(),
  /** Accumulated approved artifacts from prior phases (Epics -> HLD -> LLD -> Tests). */
  contextWindow: z.array(ContextArtifactSchema).default([]),
  plan: z.array(PlanStepSchema).default([]),
  currentStepIndex: z.number().int().default(0),
  stepOutputs: z.record(z.unknown()).default({}),
  errors: z.array(z.string()).default([]),
  finalResponse: z.string().default(''),
  gateStatus: PhaseStatusSchema.default('IN_PROGRESS'),
  /** Set when a human requested amendments; injected back into the phase agent prompt. */
  amendComments: z.string().optional(),
});
export type AgentState = z.infer<typeof AgentStateSchema>;
