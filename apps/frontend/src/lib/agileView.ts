/** Pure view logic for the Agile screens — no React, no network, fully unit-tested. */
import type { FlowStage } from '../api/flow';
import type { AgileOverview, BacklogItem, ItemStatus, Iteration } from '../api/agile';
import type { Tone } from '../components/ui/Callout';

export interface CapacityMeter { percent: number; tone: Tone; label: string; over: number }

/** How full a sprint is. Red only when over capacity; yellow when nearly full or empty. */
export function capacityMeter(committed: number, capacity: number): CapacityMeter {
  if (capacity <= 0) return { percent: 0, tone: 'neutral', label: committed > 0 ? `${committed} pts · no capacity set` : 'No capacity set', over: 0 };
  const percent = Math.round((committed / capacity) * 100);
  const over = Math.max(0, committed - capacity);
  if (over > 0) return { percent, tone: 'error', label: `${committed} of ${capacity} pts — ${over} over capacity`, over };
  if (committed === 0) return { percent: 0, tone: 'warning', label: `0 of ${capacity} pts — nothing planned yet`, over: 0 };
  if (percent >= 90) return { percent, tone: 'warning', label: `${committed} of ${capacity} pts — almost full`, over: 0 };
  return { percent, tone: 'success', label: `${committed} of ${capacity} pts`, over: 0 };
}

export const STATUS_LABEL: Record<ItemStatus, string> = {
  new: 'New', refined: 'Refined', ready: 'Ready', in_sprint: 'In sprint', in_progress: 'In progress', done: 'Done', dropped: 'Dropped',
};
export const STATUS_TONE: Record<ItemStatus, Tone> = {
  new: 'neutral', refined: 'info', ready: 'success', in_sprint: 'info', in_progress: 'warning', done: 'success', dropped: 'neutral',
};

export type BacklogFilter = 'needs-work' | 'ready' | 'sprint' | 'done' | 'dropped' | 'all';
export const FILTERS: Array<{ id: BacklogFilter; label: string; statuses: ItemStatus[] | null }> = [
  { id: 'needs-work', label: 'Needs work', statuses: ['new', 'refined'] },
  { id: 'ready', label: 'Ready', statuses: ['ready'] },
  { id: 'sprint', label: 'In sprint', statuses: ['in_sprint', 'in_progress'] },
  { id: 'done', label: 'Done', statuses: ['done'] },
  { id: 'dropped', label: 'Dropped', statuses: ['dropped'] },
  { id: 'all', label: 'All', statuses: null },
];
export function applyFilter(items: BacklogItem[], filter: BacklogFilter): BacklogItem[] {
  const f = FILTERS.find((x) => x.id === filter);
  return !f || !f.statuses ? items.filter((i) => filter === 'all' ? i.status !== 'dropped' : true) : items.filter((i) => f.statuses!.includes(i.status));
}
export function filterCounts(counts: Record<string, number> | undefined): Record<BacklogFilter, number> {
  const c = counts ?? {};
  const sum = (...k: string[]) => k.reduce((n, s) => n + (c[s] ?? 0), 0);
  return {
    'needs-work': sum('new', 'refined'), ready: sum('ready'), sprint: sum('in_sprint', 'in_progress'),
    done: sum('done'), dropped: sum('dropped'), all: sum('new', 'refined', 'ready', 'in_sprint', 'in_progress', 'done'),
  };
}

/** A stage that belongs to the one-off foundation (not to a sprint or a release). */
const isFoundation = (s: FlowStage): boolean => (s.scope ?? 'project') === 'project' && !s.iterationLabel && !s.release;

export interface SprintGroup { key: string; label: string; status: string; stages: FlowStage[] }

/** The pipeline rail for an iterative project: one-off project stages, then each sprint, then releases. */
export function groupStages(stages: FlowStage[], iterations: Iteration[] = []): SprintGroup[] {
  const groups: SprintGroup[] = [];
  const project = stages.filter(isFoundation);
  if (project.length) groups.push({ key: 'project', label: 'Foundation', status: 'project', stages: project });
  const byIter = new Map<string, FlowStage[]>();
  for (const s of stages) if (s.iterationLabel) byIter.set(s.iterationLabel, [...(byIter.get(s.iterationLabel) ?? []), s]);
  for (const [label, list] of [...byIter.entries()].sort((a, b) => (a[1][0]?.iteration ?? 0) - (b[1][0]?.iteration ?? 0))) {
    const it = iterations.find((i) => i.label === label);
    groups.push({ key: label, label, status: it?.status ?? 'active', stages: list });
  }
  const releases = new Map<string, FlowStage[]>();
  for (const s of stages) if (s.scope === 'release' && s.release) releases.set(s.release, [...(releases.get(s.release) ?? []), s]);
  for (const [code, list] of releases) groups.push({ key: code, label: `Release ${code}`, status: 'release', stages: list });
  return groups;
}

export interface NextStep { tone: Tone; title: string; body: string; action: 'start-sprint' | 'harden' | 'open-stage' | 'none'; stageSeq?: number }

/** The single most useful thing to do next, for the overview banner. */
export function nextStep(o: AgileOverview, stages: FlowStage[]): NextStep {
  if (!o.enabled) return { tone: 'info', title: 'Choose how this project is delivered', body: 'Pick Scrum or Kanban to plan work in sprints and keep a backlog.', action: 'none' };
  const it = o.currentIteration;
  const foundation = stages.filter(isFoundation);
  const open = foundation.find((s) => s.status !== 'APPROVED');
  if (open) return { tone: 'info', title: `Finish “${open.name}” first`, body: 'The foundation stages run once. Approve them, then start your first sprint.', action: 'open-stage', stageSeq: open.phase };
  if (!it) {
    const closed = (o.iterations ?? []).filter((i) => i.status === 'closed').length;
    if (o.currentRelease?.status === 'hardening') {
      const rs = stages.find((s) => s.scope === 'release' && s.release === o.currentRelease?.code);
      return { tone: 'info', title: `Release ${o.currentRelease.code} is in hardening`, body: 'Complete the release stage to close it.', action: 'open-stage', stageSeq: rs?.phase };
    }
    return closed
      ? { tone: 'success', title: 'Sprint closed — what next?', body: 'Start the next sprint, or harden and release what you have shipped.', action: 'start-sprint' }
      : { tone: 'info', title: 'Start your first sprint', body: 'Give it a goal and a capacity; refinement and planning follow.', action: 'start-sprint' };
  }
  const cur = stages.filter((s) => s.iterationLabel === it.label);
  const waiting = cur.find((s) => s.status === 'PENDING_REVIEW');
  if (waiting) return { tone: 'warning', title: `“${waiting.name}” is waiting for review`, body: `${waiting.reviewerRole} needs to review it before the sprint can move on.`, action: 'open-stage', stageSeq: waiting.phase };
  const next = cur.find((s) => s.status !== 'APPROVED');
  if (next) return { tone: 'info', title: `Next: ${next.name}`, body: it.goal ? `Sprint goal — ${it.goal}` : 'Open the stage and follow the steps.', action: 'open-stage', stageSeq: next.phase };
  return { tone: 'success', title: `${it.label} is complete`, body: 'All stages are approved.', action: 'none' };
}

export function daysLeft(endsOn: string | null, today = new Date()): number | null {
  if (!endsOn) return null;
  const end = new Date(`${endsOn}T23:59:59`);
  return Math.ceil((end.getTime() - today.getTime()) / 86_400_000);
}
