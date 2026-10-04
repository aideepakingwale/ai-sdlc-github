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
  releaseDefaults?: Record<string, unknown>;
  releaseLocks?: string[];
}
export interface Release {
  id: string; number: number; code: string; name: string; goal: string; status: 'open' | 'hardening' | 'closed';
  /** Provenance only (nothing is merged back): the release this one was forked from. */
  forkedFrom?: string | null; forkedFromId?: string | null; forkBaseline?: { release?: string; sprint?: string };
  intakeRule?: 'pool' | 'epic'; usePool?: boolean; openIterationId?: string | null; stagePreset?: string;
  /** false while a start-release setup stopped part-way (it can be resumed). */
  setupComplete?: boolean;
}
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
  /** One open sprint per release; releases run in parallel. */
  openIterations?: Iteration[];
  backlog?: Record<string, number>;
  velocity?: Array<{ sprint: string; points: number; completed: number; release?: string }>;
  averageVelocity?: number;
}
export interface BacklogItem {
  id: string; key: string; type: ItemType; title: string; description: string; acceptanceCriteria: string[];
  estimate: number | null; rank: number; status: ItemStatus; epicKey: string | null; components: string[];
  labels: string[]; iterationId: string | null; releaseId?: string | null; jiraKey: string | null; version: number; problems: string[];
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

// ---- parallel releases: the start-release questionnaire and the carried context
export type CarryKind = 'spec-section' | 'requirement' | 'decision' | 'learning';
export type CarryState = 'carried' | 'modified' | 'new' | 'retired';
export interface Question {
  id: string; type: 'text' | 'choice' | 'bool' | 'release' | 'carry' | 'epics'; title: string; required?: boolean; optional?: boolean;
  help?: string; default?: unknown; max?: number; when?: Record<string, string>; options?: Array<{ id: string; label: string }>;
}
export interface Questionnaire {
  version: number; questions: Question[]; defaults: Record<string, unknown>; locked: string[];
  releases: Array<{ id: string; code: string; name: string; status: string; canFork: boolean }>;
}
export interface ReleaseAnswers {
  name?: string; goal?: string; startFrom?: 'blank' | 'fork'; sourceRelease?: string; carry?: string[];
  unfinishedItems?: 'none' | 'all' | 'selected'; items?: string[]; doneItems?: 'context' | 'none';
  stagePreset?: string; intakeRule?: 'pool' | 'epic'; epics?: string[]; adoptExistingEpicItems?: boolean;
  usePool?: boolean; jiraLabel?: string; firstSprint?: 'later' | 'now'; resumeReleaseId?: string;
}
export interface ReleasePreview {
  valid: boolean; errors: string[]; warnings: string[]; steps: string[];
  summary: { carry?: { entries: number; bytes: number }; move?: { items: string[] }; epics?: Array<{ epic: string; poolItems: number }> };
}
export interface CarrySuggestion { id: string; kind: CarryKind; title: string; reason: string; bytes: number }
export interface CarryCandidate { id: string; kind: CarryKind; title: string; bytes: number; tooLarge: boolean }
export interface CarrySuggestions {
  source: 'ai' | 'rules'; suggestions: CarrySuggestion[]; candidates: CarryCandidate[];
  rejected: Array<{ id: string; reason: string }>; budget: { maxItems: number; maxBytes: number };
}
export interface CarryView {
  forkedFrom: string | null; baseline: { release?: string; sprint?: string };
  carried: Array<{ id: string; kind: CarryKind; title: string; state: CarryState; reason: string; bytes: number }>;
  new: Array<{ id: string; kind: CarryKind; title: string; state: 'new' }>;
  counts: Partial<Record<CarryState, number>>; sourceChanged: string[]; budget: { maxItems: number; maxBytes: number };
}
export interface ReleaseStartResult { release: Release; progress: Record<string, unknown> }

const base = (p: string) => `/api/projects/${p}/agile`;

export const agileApi = {
  overview: (p: string, release?: string) => api.get<AgileOverview>(`${base(p)}${release ? `?release=${encodeURIComponent(release)}` : ''}`),
  enable: (p: string, body: { methodology: Methodology; sprintDays: number; defaultCapacity: number; wipLimit?: number | null; indexStrategy: string; autoMinScore: number }) =>
    api.post<AgileOverview>(`${base(p)}/enable`, body),
  startSprint: (p: string, body: { goal: string; capacity?: number; releaseId?: string }) => api.post<Iteration>(`${base(p)}/sprints`, body),
  cancelSprint: (p: string, id: string) => api.post<Iteration>(`${base(p)}/sprints/${id}/cancel`),
  harden: (p: string, releaseId: string) => api.post<Release>(`${base(p)}/releases/${releaseId}/harden`),
  backlog: (p: string, q?: { status?: string; iteration?: string; q?: string; release?: string; scope?: 'all' | 'pool' | 'release' | 'eligible' }) => {
    const qs = new URLSearchParams();
    if (q?.release) qs.set('release', q.release);
    if (q?.scope && q.scope !== 'all') qs.set('scope', q.scope);
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
  addToSprint: (p: string, ref: string, force = false, sprintId?: string) => api.post<BacklogItem>(`${base(p)}/backlog/${ref}/sprint`, { force, sprintId }),
  removeFromSprint: (p: string, ref: string) => api.del<BacklogItem>(`${base(p)}/backlog/${ref}/sprint`),
  proposal: (p: string, phase: number, kind: 'refine' | 'plan' | 'delta') =>
    api.get<{ proposal: Proposal | null }>(`${base(p)}/proposals?phase=${phase}&kind=${kind}`),
  editProposal: (p: string, id: string, payload: unknown, version: number) =>
    request(`${base(p)}/proposals/${id}`, 'PATCH', { payload, version }) as Promise<Proposal>,
  jira: (p: string) => api.get<JiraStatus>(`${base(p)}/jira`),
  jiraSync: (p: string, full = false) => api.post<Record<string, unknown>>(`${base(p)}/jira/sync`, { full }),
  index: (p: string) => api.get<IndexStatus>(`${base(p)}/index`),
  updateSettings: (p: string, body: Record<string, unknown>) => request(`${base(p)}/settings`, 'PATCH', body) as Promise<AgileOverview>,
  // releases
  questions: (p: string) => api.get<Questionnaire>(`${base(p)}/release-setup/questions`),
  previewRelease: (p: string, a: ReleaseAnswers) => api.post<ReleasePreview>(`${base(p)}/release-setup/preview`, a),
  startRelease: (p: string, a: ReleaseAnswers) => api.post<ReleaseStartResult>(`${base(p)}/release-setup/start`, a),
  suggestCarry: (p: string, releaseId: string, body: { scopeText: string; components?: string[]; limit?: number; useAi?: boolean }) =>
    api.post<CarrySuggestions>(`${base(p)}/releases/${releaseId}/carry/suggest`, body),
  carry: (p: string, releaseId: string) => api.get<CarryView>(`${base(p)}/releases/${releaseId}/carry`),
  extendCarry: (p: string, releaseId: string, items: string[]) =>
    api.post<{ carried: string[]; rejected: Array<{ id: string; reason: string }> }>(`${base(p)}/releases/${releaseId}/carry`, { items }),
  claim: (p: string, releaseId: string, items: string[]) => api.post<{ claimed: string[]; skipped: string[] }>(`${base(p)}/releases/${releaseId}/claim`, { items }),
  releaseEpics: (p: string, releaseId: string) => api.get<{ epics: Array<{ epicKey: string; title: string; jiraKey: string | null }> }>(`${base(p)}/releases/${releaseId}/epics`),
  mapEpic: (p: string, releaseId: string, epic: string, adoptExisting = false) =>
    api.post<{ epicKey: string; adopted?: string[] }>(`${base(p)}/releases/${releaseId}/epics`, { epic, adoptExisting }),
  unmapEpic: (p: string, epic: string) => api.del<{ ok: boolean }>(`${base(p)}/epics/${epic}/release`),
  reconcile: (p: string) => api.post<{ revisited: number }>(`${base(p)}/reconcile`),
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
