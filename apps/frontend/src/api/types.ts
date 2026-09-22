// Frontend mirrors of the shared API contracts (kept dependency-free so the
// SPA bundle doesn't pull the node-oriented shared package).

export type Role = 'SUPER_ADMIN' | 'PROJECT_MANAGER' | 'PO' | 'SA' | 'TA' | 'QA' | 'DEVOPS' | 'DEV';

export type PhaseRole = 'PO' | 'SA' | 'TA' | 'QA' | 'DEVOPS' | 'DEV';

export const ROLE_LABELS: Record<Role, string> = {
  SUPER_ADMIN: 'Super Admin',
  PROJECT_MANAGER: 'Project Manager',
  PO: 'Product Owner (P1)',
  SA: 'Solution Architect (P2)',
  TA: 'Technical Architect (P3)',
  QA: 'QA Lead (P4)',
  DEVOPS: 'DevOps Engineer (P5)',
  DEV: 'Developer (P6)',
};

export interface AuthConfig {
  mode: 'keycloak' | 'local';
  ssoLoginUrl: string | null;
}

export interface ProjectMember {
  userId: string;
  email: string;
  displayName: string;
  role: PhaseRole;
  addedAt: string;
}

export interface DirectoryUser {
  id: string;
  email: string;
  displayName: string;
  role: Role;
}

export type PhaseStatus =
  | 'NOT_STARTED'
  | 'IN_PROGRESS'
  | 'PENDING_REVIEW'
  | 'APPROVED'
  | 'AMEND_REQUESTED'
  | 'ESCALATED';

export interface User {
  id: string;
  email: string;
  displayName: string;
  role: Role;
}

export interface Project {
  id: string;
  name: string;
  status: string;
  currentPhase: number;
  createdAt: string;
  techStack?: string;
}

// Configurable technology catalog served by GET /api/meta/tech-catalog:
// programming language → version(s) → framework(s). Replaces the old hardcoded
// TECH_STACKS list; the New Project form builds a structured stack from this.
export interface TechCatalogLanguage {
  name: string;
  versions: string[];
  frameworks: string[];
}
export interface TechCatalog {
  languages: TechCatalogLanguage[];
}

export interface PhaseStateView {
  phase: number;
  name: string;
  status: PhaseStatus;
  reviewerRole: Role;
  updatedAt: string;
  reviewedBy: string | null;
  /** Server-computed: may the current viewer review this gate right now? */
  canReview: boolean;
}

export interface Artefact {
  id: string;
  projectId: string;
  phase: number;
  type: string;
  title: string;
  url: string | null;
  version: number;
  createdAt: string;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  phase: number;
  createdAt: string;
}

export interface AuditEvent {
  id: string;
  timestamp: string;
  projectId: string;
  phase: number | null;
  agentRole: string;
  event: string;
  provider: string | null;
  model: string | null;
  promptTokens: number | null;
  completionTokens: number | null;
  artefactHash: string | null;
  humanReviewer: string | null;
  detail: Record<string, unknown>;
}

/** Viz: emitted by the planner before execution — the run's blueprint. */
export interface PlanEvent {
  type: 'plan';
  stage: number;
  stageName: string;
  template: number;
  persona: string;
  tier: 'non_llm' | 'local' | 'frontier';
  steps: Array<{ id: string; tool: string; description: string }>;
  expectedTools: string[];
  nodes: string[];
}

export interface PlanPreview {
  projectId: string;
  currentPhase: number;
  workflowVersion: number;
  nodes: string[];
  parallel: boolean;
  stages: Array<{
    seq: number;
    key: string;
    name: string;
    template: number;
    persona: string;
    reviewerRole: string;
    tier: 'non_llm' | 'local' | 'frontier';
    steps: Array<{ id: string; tool: string; description: string }>;
    expectedTools: string[];
    skills: Array<{ id: string; name: string; tier: string }>;
  }>;
  pendingGates: Array<{ seq: number; name: string; reviewerRole: string }>;
}

export type StreamEvent =
  | { type: 'session'; projectId: string; sessionId: string; phase: number }
  | { type: 'node'; node: string; label: string }
  | { type: 'model'; tier: 'non_llm' | 'local' | 'frontier'; label: string }
  | PlanEvent
  | { type: 'tool_call'; tool: string; status: 'start' | 'success' | 'error'; summary?: string }
  | { type: 'token'; content: string }
  | { type: 'artifact'; artifact: { type: string; title: string; url?: string; key?: string } }
  | { type: 'gate'; phase: number; status: PhaseStatus; reviewerRole: Role }
  | { type: 'done'; finalResponse: string; phase: number; gateStatus: PhaseStatus }
  | { type: 'error'; code: string; message: string };

export interface ProjectDetail {
  project: Project;
  sessionId: string | null;
  contextWindow: Array<{ phase: number; type: string; title: string; summary: string; ref?: { url?: string; key?: string } }>;
  phaseStates: PhaseStateView[];
  me: { membershipRole: PhaseRole | null; canManageTeam: boolean };
  messages: ChatMessage[];
}
