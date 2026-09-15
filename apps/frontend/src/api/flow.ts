export type StageColor = 'slate' | 'blue' | 'amber' | 'orange' | 'emerald' | 'red';

export interface FlowStage {
  phase: number;
  key: string;
  name: string;
  persona: string;
  template: number;
  reviewerRole: string;
  team: string[];
  teamMembers: Array<{ displayName: string; email: string; role: string }>;
  inputs: string[];
  outputs: string[];
  dependsOn: string[];
  level: number;
  status: string;
  color: StageColor;
  artifactCount: number;
  reviewedBy: string | null;
  updatedAt: string | null;
  assignee: { displayName: string; email: string; role: string } | null;
  canReview: boolean;
  canRetrigger: boolean;
}

export interface ProjectFlow {
  projectId: string;
  name: string;
  techStack: string | null;
  currentPhase: number;
  status: string;
  isManager: boolean;
  viewerRole: string;
  workflowVersion: number;
  levels: number[][];
  stages: FlowStage[];
}

// ---------------- workflow designer ----------------
export interface StageConfig {
  key: string;
  name: string;
  template: number;
  /** Primary gate reviewer (kept for runtime/gate-state compatibility). */
  reviewerRole: string;
  /** Every role allowed to sign this stage's gate. Empty = [reviewerRole]. */
  reviewerRoles?: string[];
  team: string[];
  /** Roles that may view the stage's artifacts. Empty = the whole team. */
  readRoles?: string[];
  /** Roles that may run/retrigger the stage. Empty = the whole team. */
  writeRoles?: string[];
  inputs: string[];
  outputs: string[];
  dependsOn: string[];
  // Custom phase type (template 7): the PM defines the phase by config.
  persona?: string;
  promptId?: string;
  tools?: string[];
}

export interface WorkflowView {
  config: { stages: StageConfig[] };
  version: number;
  stages: Array<StageConfig & { seq: number; level: number; persona: string }>;
  levels: number[][];
}

export const TEMPLATE_NAMES: Record<number, string> = {
  1: 'Product Owner (requirements)',
  2: 'Solution Architect (HLD)',
  3: 'Technical Architect (LLD)',
  4: 'QA Lead (tests)',
  5: 'DevOps Engineer (CI/CD)',
  6: 'Developer (code + build loop)',
  7: 'Custom (define your own phase)',
};

export const PHASE_ROLE_OPTIONS = ['PO', 'SA', 'TA', 'QA', 'DEVOPS', 'DEV'];

/** Tailwind class tokens per stage state (color-coded pipeline). */
export const COLOR_CLASSES: Record<StageColor, { dot: string; ring: string; chip: string; label: string }> = {
  slate: { dot: 'bg-slate-300', ring: 'border-slate-200', chip: 'bg-slate-100 text-slate-500', label: 'Planned' },
  blue: { dot: 'bg-blue-500 animate-pulse', ring: 'border-blue-300', chip: 'bg-blue-100 text-blue-700', label: 'In progress' },
  amber: { dot: 'bg-amber-500', ring: 'border-amber-300', chip: 'bg-amber-100 text-amber-700', label: 'In review' },
  orange: { dot: 'bg-orange-500', ring: 'border-orange-300', chip: 'bg-orange-100 text-orange-700', label: 'Amend requested' },
  emerald: { dot: 'bg-emerald-500', ring: 'border-emerald-300', chip: 'bg-emerald-100 text-emerald-700', label: 'Completed' },
  red: { dot: 'bg-red-500', ring: 'border-red-300', chip: 'bg-red-100 text-red-700', label: 'Escalated' },
};
