import type { FlowStage } from '../api/flow';

export interface Attention { phase: number; name: string; kind: 'review' | 'escalated' | 'amend' | 'stale' | 'failed'; text: string }

const ORDER: Attention['kind'][] = ['escalated', 'failed', 'review', 'amend', 'stale'];

/** Stages that need a person, most urgent first. */
export function attentionItems(stages: FlowStage[]): Attention[] {
  const out: Attention[] = [];
  for (const s of stages) {
    if (s.status === 'ESCALATED') out.push({ phase: s.phase, name: s.name, kind: 'escalated', text: 'Escalated to a human developer' });
    else if (s.status === 'FAILED') out.push({ phase: s.phase, name: s.name, kind: 'failed', text: 'Generation failed' });
    else if (s.status === 'PENDING_REVIEW') out.push({ phase: s.phase, name: s.name, kind: 'review', text: `Waiting for ${s.reviewerRole} to sign off` });
    else if (s.status === 'AMEND_REQUESTED') out.push({ phase: s.phase, name: s.name, kind: 'amend', text: 'Changes requested; regenerating' });
    if (s.stale) out.push({ phase: s.phase, name: s.name, kind: 'stale', text: s.staleReason ?? 'An upstream input changed' });
  }
  return out.sort((a, b) => ORDER.indexOf(a.kind) - ORDER.indexOf(b.kind) || a.phase - b.phase);
}

/** Stages grouped by workflow level; stages in one group can run in parallel. */
export function groupByLevel(stages: FlowStage[], levels: number[][] | undefined): FlowStage[][] {
  if (levels?.length) {
    const by = new Map(stages.map((s) => [s.phase, s]));
    const g = levels.map((l) => l.map((p) => by.get(p)).filter((s): s is FlowStage => Boolean(s))).filter((l) => l.length);
    if (g.length) return g;
  }
  return stages.map((s) => [s]);
}
