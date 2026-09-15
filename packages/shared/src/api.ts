import { z } from 'zod';
import { ArtifactTypeSchema, PhaseRoleSchema, PhaseStatusSchema, RoleSchema } from './phases.js';

// ---------- Auth ----------
export const LoginRequestSchema = z.object({
  email: z.string().email(),
  password: z.string().min(8).max(256),
});
export type LoginRequest = z.infer<typeof LoginRequestSchema>;

export const UserPublicSchema = z.object({
  id: z.string(),
  email: z.string(),
  displayName: z.string(),
  role: RoleSchema,
});
export type UserPublic = z.infer<typeof UserPublicSchema>;

// ---------- Chat ----------
export const ChatRequestSchema = z.object({
  /** Omit to create a new project from this first message. */
  projectId: z.string().uuid().optional(),
  message: z.string().min(1).max(32_000),
});
export type ChatRequest = z.infer<typeof ChatRequestSchema>;

/** SSE events streamed from POST /api/chat (Module 7 §3). */
export const StreamEventSchema = z.discriminatedUnion('type', [
  z.object({
    type: z.literal('session'),
    projectId: z.string(),
    sessionId: z.string(),
    phase: z.number().int(),
  }),
  z.object({
    type: z.literal('node'),
    node: z.enum(['guardrail', 'planner', 'executor', 'synthesizer', 'formatter', 'fact_check', 'agent', 'compressor']),
    label: z.string(),
  }),
  z.object({
    type: z.literal('model'),
    tier: z.enum(['non_llm', 'local', 'frontier']),
    label: z.string(),
  }),
  z.object({
    type: z.literal('tool_call'),
    tool: z.string(),
    status: z.enum(['start', 'success', 'error']),
    summary: z.string().optional(),
  }),
  z.object({ type: z.literal('token'), content: z.string() }),
  z.object({
    type: z.literal('artifact'),
    artifact: z.object({
      type: ArtifactTypeSchema,
      title: z.string(),
      url: z.string().optional(),
      key: z.string().optional(),
    }),
  }),
  z.object({
    type: z.literal('gate'),
    phase: z.number().int(),
    status: PhaseStatusSchema,
    reviewerRole: RoleSchema,
  }),
  z.object({
    type: z.literal('done'),
    finalResponse: z.string(),
    phase: z.number().int(),
    gateStatus: PhaseStatusSchema,
  }),
  z.object({ type: z.literal('error'), code: z.string(), message: z.string() }),
]);
export type StreamEvent = z.infer<typeof StreamEventSchema>;

// ---------- Gates ----------
export const GateReviewRequestSchema = z.object({
  decision: z.enum(['APPROVE', 'AMEND']),
  comments: z.string().max(8_000).optional(),
});
export type GateReviewRequest = z.infer<typeof GateReviewRequestSchema>;

export const GateReviewResponseSchema = z.object({
  projectId: z.string(),
  phase: z.number().int(),
  status: PhaseStatusSchema,
  nextPhase: z.number().int().nullable(),
});
export type GateReviewResponse = z.infer<typeof GateReviewResponseSchema>;

export const PhaseStateViewSchema = z.object({
  phase: z.number().int(),
  name: z.string(),
  status: PhaseStatusSchema,
  reviewerRole: RoleSchema,
  updatedAt: z.string(),
  reviewedBy: z.string().nullable(),
  /** True when the requesting viewer may review this gate right now. */
  canReview: z.boolean().default(false),
});
export type PhaseStateView = z.infer<typeof PhaseStateViewSchema>;

// ---------- Auth / RBAC ----------
export const AuthConfigSchema = z.object({
  mode: z.enum(['keycloak', 'local']),
  /** Browser-facing OIDC authorization entry point (code flow), when keycloak. */
  ssoLoginUrl: z.string().nullable(),
});
export type AuthConfig = z.infer<typeof AuthConfigSchema>;

export const AddMemberRequestSchema = z.object({
  email: z.string().email(),
  /** Phase role this member holds ON THIS PROJECT; must match their platform role. */
  role: PhaseRoleSchema,
});
export type AddMemberRequest = z.infer<typeof AddMemberRequestSchema>;

export const ProjectMemberViewSchema = z.object({
  userId: z.string(),
  email: z.string(),
  displayName: z.string(),
  role: PhaseRoleSchema,
  addedAt: z.string(),
});
export type ProjectMemberView = z.infer<typeof ProjectMemberViewSchema>;

export const CreateProjectRequestSchema = z.object({
  name: z.string().min(3).max(120),
});
export type CreateProjectRequest = z.infer<typeof CreateProjectRequestSchema>;

// ---------- Projects & artifacts ----------
export const ProjectViewSchema = z.object({
  id: z.string(),
  name: z.string(),
  status: z.string(),
  currentPhase: z.number().int(),
  createdAt: z.string(),
});
export type ProjectView = z.infer<typeof ProjectViewSchema>;

export const ArtefactViewSchema = z.object({
  id: z.string(),
  projectId: z.string(),
  phase: z.number().int(),
  type: ArtifactTypeSchema,
  title: z.string(),
  url: z.string().nullable(),
  version: z.number().int(),
  createdAt: z.string(),
});
export type ArtefactView = z.infer<typeof ArtefactViewSchema>;

// ---------- Audit ----------
export const AuditEventSchema = z.object({
  id: z.string(),
  timestamp: z.string(),
  projectId: z.string(),
  phase: z.number().int().nullable(),
  agentRole: z.string(),
  event: z.string(),
  provider: z.string().nullable(),
  model: z.string().nullable(),
  promptTokens: z.number().int().nullable(),
  completionTokens: z.number().int().nullable(),
  artefactHash: z.string().nullable(),
  humanReviewer: z.string().nullable(),
  detail: z.record(z.unknown()).default({}),
});
export type AuditEvent = z.infer<typeof AuditEventSchema>;

// ---------- Build recovery ----------
export const BuildLoopStateSchema = z.object({
  runId: z.string(),
  projectId: z.string(),
  state: z.enum(['PIPELINE_RUNNING', 'ANALYSING_FAILURE', 'FIXING', 'SUCCEEDED', 'ESCALATED']),
  iterationCount: z.number().int(),
  lastRootCause: z.string().nullable(),
  failedJobIds: z.array(z.string()),
  updatedAt: z.string(),
});
export type BuildLoopState = z.infer<typeof BuildLoopStateSchema>;
