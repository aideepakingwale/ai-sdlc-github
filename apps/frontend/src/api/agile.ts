import { api } from './client';

export type Methodology = 'waterfall' | 'scrum' | 'kanban';
export type ItemType = 'epic' | 'story' | 'bug' | 'task';
export type ItemStatus = 'new' | 'refined' | 'ready' | 'in_sprint' | 'in_progress' | 'done' | 'dropped';

export interface AgileSettings {
  sprintDays: number;
  defaultCapacity: number;
  wipLimit: number | null;
  indexStrategy: 'index-branch' | 'default-branch';
  autoMinScore: number;
}
export interface Release { id: string; number: number; code: string; name: string; goal: string; status: 'open' | 'hardening' | 'closed' }
export interface Iteration {
  id: string; number: number; label: string; releaseId: string; goal: string;
  status: 'planned' | 'active' | 'closed' | 'cancelled'; capacity: number;
  startsOn: string | null; endsOn: string | null;
  summary: { velocity?: number; completed?: number; carried?: number; committedPoints?: number; completionRate?: number | null };
}
export interface AgileOverview {
  enabled: boolean;
  methodology: Methodology;
  permissions: { canManage: boolean; canRun: boolean };
  settings?: AgileSettings;
  releases?: Release[];
  iterations?: Iteration[];
  currentIteration?: Iteration | null;
  currentRelease?: Release | null;
  backlog?: Record<string, number>;
  velocity?: Array<{ sprint: string; points: number; completed: number }>;
  averageVelocity?: number;
}
export interface BacklogItem {
  id: string; key: string; type: ItemType; title: string; description: string; acceptanceCriteria: string[];
  estimate: number | null; rank: number; status: ItemStatus; epicKey: string | null; components: string[];
  labels: string[]; iterationId: string | null; jiraKey: string | null; version: number; problems: string[];
  createdAt: string; updatedAt: string;
}
export interface BacklogList { items: BacklogItem[]; summary: { count: number; points: number } }

export interface RefineOp {
  ref: string; op: 'create' | 'update' | 'drop'; target: string | null; type: ItemType; title: string;
  description: string; acceptanceCriteria: string[]; estimate: number | null; components: string[];
  epic: string | null; rationale: string;
}
export interface Proposal {
  id: string; kind: 'refine' | 'plan' | 'delta'; phase: number; iterationId: string | null;
  status: 'proposed' | 'applied' | 'rejected' | 'superseded'; version: number; warnings: string[]; createdAt: string;
  payload: {
    summary?: string; ops?: RefineOp[];                                          // refine
    goal?: string; items?: Array<{ key: string; title: string; estimate: number; reason?: string }>;
    points?: number; capacity?: number; risks?: string[];                        // plan
    changes?: Array<{ component: string; section: string; op: string; content: string; rationale: string }>; // delta
  };
}
export interface JiraStatus {
  enabled: boolean; projectKey: string | null; reason?: string; lastRunAt?: string | null;
  lastStatus?: string | null; lastError?: string | null; watermark?: string | null;
  stats?: { created?: number; updated?: number; conflicts?: number; errors?: string[] };
}
export interface IndexStatus {
  initialised: boolean; generation?: number; files?: number; tiers?: Record<string, number>;
  currentRelease?: string | null; currentSprint?: string | null; publishedCommit?: string | null;
  unpublished?: { added: number; modified: number; deleted: number };
  drift?: Array<{ path: string; kind: string }>; specs?: string[]; strategy?: string;
}

const base = (p: string) => `/api/projects/${p}/agile`;

export const agileApi = {
  overview: (p: string) => api.get<AgileOverview>(base(p)),
  enable: (p: string, body: { methodology: Methodology; sprintDays: number; defaultCapacity: number; wipLimit?: number | null; indexStrategy: string; autoMinScore: number }) =>
    api.post<AgileOverview>(`${base(p)}/enable`, body),
  startSprint: (p: string, body: { goal: string; capacity?: number }) => api.post<Iteration>(`${base(p)}/sprints`, body),
  cancelSprint: (p: string, id: string) => api.post<Iteration>(`${base(p)}/sprints/${id}/cancel`),
  harden: (p: string, releaseId: string) => api.post<Release>(`${base(p)}/releases/${releaseId}/harden`),
  backlog: (p: string, q?: { status?: string; iteration?: string; q?: string }) => {
    const qs = new URLSearchParams();
    if (q?.status) qs.set('status', q.status);
    if (q?.iteration) qs.set('iteration', q.iteration);
    if (q?.q) qs.set('q', q.q);
    return api.get<BacklogList>(`${base(p)}/backlog${qs.size ? `?${qs}` : ''}`);
  },
  createItem: (p: string, body: Partial<BacklogItem> & { title: string }) => api.post<BacklogItem>(`${base(p)}/backlog`, body),
  patchItem: (p: string, ref: string, body: Record<string, unknown>) =>
    request(`${base(p)}/backlog/${ref}`, 'PATCH', body) as Promise<BacklogItem>,
  setStatus: (p: string, ref: string, status: ItemStatus, expectedVersion?: number) =>
    api.post<BacklogItem>(`${base(p)}/backlog/${ref}/status`, { status, expectedVersion }),
  move: (p: string, ref: string, pos: { before?: string; after?: string }) => api.post<BacklogItem>(`${base(p)}/backlog/${ref}/move`, pos),
  addToSprint: (p: string, ref: string, force = false) => api.post<BacklogItem>(`${base(p)}/backlog/${ref}/sprint`, { force }),
  removeFromSprint: (p: string, ref: string) => api.del<BacklogItem>(`${base(p)}/backlog/${ref}/sprint`),
  proposal: (p: string, phase: number, kind: 'refine' | 'plan' | 'delta') =>
    api.get<{ proposal: Proposal | null }>(`${base(p)}/proposals?phase=${phase}&kind=${kind}`),
  editProposal: (p: string, id: string, payload: unknown, version: number) =>
    request(`${base(p)}/proposals/${id}`, 'PATCH', { payload, version }) as Promise<Proposal>,
  jira: (p: string) => api.get<JiraStatus>(`${base(p)}/jira`),
  jiraSync: (p: string, full = false) => api.post<Record<string, unknown>>(`${base(p)}/jira/sync`, { full }),
  index: (p: string) => api.get<IndexStatus>(`${base(p)}/index`),
};

// The shared client has no PATCH helper; keep the credentialed JSON PATCH here.
async function request(path: string, method: string, body: unknown): Promise<unknown> {
  const res = await fetch(path, { method, credentials: 'include', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  if (!res.ok) {
    const j = (await res.json().catch(() => null)) as { error?: { message?: string } } | null;
    throw new Error(j?.error?.message ?? `Request failed (${res.status})`);
  }
  return res.json();
}
