import { useState } from 'react';
import type { Iteration } from '../../api/agile';
import type { ProjectFlow } from '../../api/flow';
import type { PhaseStateView } from '../../api/types';
import { groupStages } from '../../lib/agileView';
import PhaseTracker from '../PhaseTracker';
import { Icon } from '../ui/Icon';

const TAG: Record<string, string> = { active: 'bg-blue-500/30 text-blue-200', planned: 'bg-amber-500/30 text-amber-200', closed: 'bg-emerald-500/20 text-emerald-200', cancelled: 'bg-white/10 text-slate-400' };

/** Sidebar rail for iterative projects: foundation stages, then one collapsible group per sprint. */
export default function SprintRail({ flow, iterations, currentPhase, selected, onSelect }: {
  flow: ProjectFlow; iterations: Iteration[]; currentPhase: number; selected?: number; onSelect: (seq: number) => void;
}) {
  const groups = groupStages(flow.stages, iterations);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const isCollapsed = (key: string, status: string): boolean => collapsed[key] ?? (status === 'closed');
  return (
    <div className="space-y-2">
      {groups.map((g) => {
        const states: PhaseStateView[] = g.stages.map((s) => ({
          phase: s.phase, name: s.name.replace(/ · [SR]-?\d+.*$/, ''), status: s.status as PhaseStateView['status'],
          reviewerRole: s.reviewerRole as PhaseStateView['reviewerRole'], updatedAt: s.updatedAt ?? '', reviewedBy: s.reviewedBy,
          canReview: s.canReview, stale: s.stale, staleReason: s.staleReason,
        }));
        const shut = isCollapsed(g.key, g.status);
        const done = g.stages.filter((s) => s.status === 'APPROVED').length;
        return (
          <section key={g.key} aria-label={g.label}>
            <button type="button" onClick={() => setCollapsed({ ...collapsed, [g.key]: !shut })} aria-expanded={!shut}
              className="flex w-full items-center gap-1.5 px-1 py-1 text-left text-[11px] font-semibold uppercase tracking-wide text-slate-400 hover:text-slate-200">
              <Icon name="chevron-right" size={12} className={shut ? '' : 'rotate-90'} />
              {g.label}
              {['active', 'planned', 'closed', 'cancelled'].includes(g.status) && <span className={`rounded px-1 text-[9px] normal-case ${TAG[g.status]}`}>{g.status}</span>}
              <span className="ml-auto font-normal normal-case text-slate-500">{done}/{g.stages.length}</span>
            </button>
            {!shut && <PhaseTracker states={states} currentPhase={currentPhase} selected={selected} onSelect={onSelect} />}
          </section>
        );
      })}
    </div>
  );
}
