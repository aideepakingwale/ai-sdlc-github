import { z } from 'zod';

/**
 * Two-tier role model:
 * - Platform roles: SUPER_ADMIN (platform management + break-glass gate
 *   override, audited) and PROJECT_MANAGER (creates projects, manages team
 * membership; segregation of duties — PMs never approve gates).
 * - Phase roles: held by team members; gate authority additionally requires
 *   project membership with the matching role (enforced in GateService).
 */
export const PlatformRoleSchema = z.enum(['SUPER_ADMIN', 'PROJECT_MANAGER']);
export type PlatformRole = z.infer<typeof PlatformRoleSchema>;

export const PhaseRoleSchema = z.enum(['PO', 'SA', 'TA', 'QA', 'DEVOPS', 'DEV']);
export type PhaseRole = z.infer<typeof PhaseRoleSchema>;

export const RoleSchema = z.enum(['SUPER_ADMIN', 'PROJECT_MANAGER', 'PO', 'SA', 'TA', 'QA', 'DEVOPS', 'DEV']);
export type Role = z.infer<typeof RoleSchema>;

export function isPhaseRole(role: Role): role is PhaseRole {
  return PhaseRoleSchema.safeParse(role).success;
}

/** Priority used when an IdP user carries multiple realm roles (JIT provisioning). */
export const ROLE_PRIORITY: readonly Role[] = [
  'SUPER_ADMIN',
  'PROJECT_MANAGER',
  'PO',
  'SA',
  'TA',
  'QA',
  'DEVOPS',
  'DEV',
];

export function primaryRole(roles: string[]): Role | null {
  for (const candidate of ROLE_PRIORITY) {
    if (roles.includes(candidate)) return candidate;
  }
  return null;
}

/** Gate / phase lifecycle status, stored in DynamoDB PhaseState. */
export const PhaseStatusSchema = z.enum([
  'NOT_STARTED',
  'IN_PROGRESS',
  'PENDING_REVIEW',
  'APPROVED',
  'AMEND_REQUESTED',
  'ESCALATED',
]);
export type PhaseStatus = z.infer<typeof PhaseStatusSchema>;

/** Every artifact type the six phase agents can produce. */
export const ArtifactTypeSchema = z.enum([
  'EPIC',
  'USER_STORY',
  'PRD',
  'HLD',
  'ADR',
  'STRUCTURIZR_DSL',
  'CLOUDCRAFT_JSON',
  'LLD',
  'PLANTUML',
  'OPENAPI',
  'DBML',
  'CDK',
  'TEST_STRATEGY',
  'XRAY_TESTS',
  'K6_SCRIPT',
  'POSTMAN_COLLECTION',
  'RTM',
  'GITHUB_ACTIONS',
  'DOCKERFILE',
  'GRAFANA_DASHBOARD',
  'APP_CODE',
  'UNIT_TESTS',
  'PULL_REQUEST',
]);
export type ArtifactType = z.infer<typeof ArtifactTypeSchema>;

export interface PhaseDefinition {
  id: number;
  key: string;
  name: string;
  agentPersona: string;
  /** Phase role whose sign-off advances the gate (SUPER_ADMIN may override). */
  reviewerRole: PhaseRole;
  produces: ArtifactType[];
}

/** The six-phase pipeline (Master Spec §4). Order is the pipeline order. */
export const PHASES: readonly PhaseDefinition[] = [
  {
    id: 1,
    key: 'product-owner',
    name: 'Requirements & Product Definition',
    agentPersona: 'Product Owner',
    reviewerRole: 'PO',
    produces: ['EPIC', 'USER_STORY', 'PRD'],
  },
  {
    id: 2,
    key: 'solution-architect',
    name: 'Solution Architecture',
    agentPersona: 'Solution Architect',
    reviewerRole: 'SA',
    produces: ['HLD', 'ADR', 'STRUCTURIZR_DSL', 'CLOUDCRAFT_JSON'],
  },
  {
    id: 3,
    key: 'technical-architect',
    name: 'Technical Design',
    agentPersona: 'Technical Architect',
    reviewerRole: 'TA',
    produces: ['LLD', 'PLANTUML', 'OPENAPI', 'DBML', 'CDK'],
  },
  {
    id: 4,
    key: 'qa-lead',
    name: 'Test Engineering',
    agentPersona: 'QA Lead',
    reviewerRole: 'QA',
    produces: ['TEST_STRATEGY', 'XRAY_TESTS', 'K6_SCRIPT', 'POSTMAN_COLLECTION', 'RTM'],
  },
  {
    id: 5,
    key: 'devops-engineer',
    name: 'CI/CD & Observability',
    agentPersona: 'DevOps Engineer',
    reviewerRole: 'DEVOPS',
    produces: ['GITHUB_ACTIONS', 'DOCKERFILE', 'GRAFANA_DASHBOARD'],
  },
  {
    id: 6,
    key: 'developer',
    name: 'Implementation & Delivery',
    agentPersona: 'Senior Developer',
    reviewerRole: 'DEV',
    produces: ['APP_CODE', 'UNIT_TESTS', 'PULL_REQUEST'],
  },
] as const;

export const MIN_PHASE = 1;
export const MAX_PHASE = 6;

export function getPhase(id: number): PhaseDefinition {
  const phase = PHASES.find((p) => p.id === id);
  if (!phase) throw new RangeError(`Unknown phase id ${id}; expected ${MIN_PHASE}..${MAX_PHASE}`);
  return phase;
}

/**
 * Platform-level gate check: SUPER_ADMIN overrides (break-glass, audited);
 * phase roles must match the phase's reviewer role. PROJECT_MANAGER is never
 * a reviewer. NOTE: for phase roles this is necessary but not
 * sufficient — GateService additionally requires membership in the project
 * with this role.
 */
export function canReviewPhase(role: Role, phaseId: number): boolean {
  if (role === 'SUPER_ADMIN') return true;
  if (role === 'PROJECT_MANAGER') return false;
  return getPhase(phaseId).reviewerRole === role;
}

/** May this role create projects and manage team membership? */
export function canManageProjects(role: Role): boolean {
  return role === 'SUPER_ADMIN' || role === 'PROJECT_MANAGER';
}
