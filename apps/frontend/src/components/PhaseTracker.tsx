import type { PhaseStateView } from '../api/types';

const STATUS_STYLES: Record<string, string> = {
  NOT_STARTED: 'bg-slate-200 text-slate-500',
  IN_PROGRESS: 'bg-blue-100 text-blue-700 animate-pulse',
  PENDING_REVIEW: 'bg-amber-100 text-amber-700',
  APPROVED: 'bg-emerald-100 text-emerald-700',
  AMEND_REQUESTED: 'bg-orange-100 text-orange-700',
  ESCALATED: 'bg-red-100 text-red-700',
};

const STATUS_ICON: Record<string, string> = {
  NOT_STARTED: '○',
  IN_PROGRESS: '◐',
  PENDING_REVIEW: '⏸',
  APPROVED: '✓',
  AMEND_REQUESTED: '↺',
  ESCALATED: '⚠',
};

/**
 * Pipeline Rail: the project's stages as a clickable vertical stepper —
 * the primary in-project navigation for the stage-centric workspace. `selected`
 * highlights the active stage; `onSelect` drives the Stage Workspace.
 */
export default function PhaseTracker({
  states,
  currentPhase,
  selected,
  onSelect,
}: {
  states: PhaseStateView[];
  currentPhase: number;
  selected?: number;
  onSelect?: (phase: number) => void;
}) {
  return (
    <div className="space-y-1">
      {states.map((s) => {
        const isSelected = s.phase === selected;
        const Wrapper = onSelect ? 'button' : 'div';
        return (
          <Wrapper
            key={s.phase}
            {...(onSelect ? { onClick: () => onSelect(s.phase), type: 'button' as const } : {})}
            className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs transition ${
              isSelected
                ? 'bg-brand-600/30 ring-1 ring-brand-400/60'
                : s.phase === currentPhase
                  ? 'bg-white/10 ring-1 ring-white/20'
                  : onSelect
                    ? 'hover:bg-white/5'
                    : ''
            }`}
          >
            <span
              className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-bold ${STATUS_STYLES[s.status] ?? ''}`}
              title={s.status}
            >
              {STATUS_ICON[s.status] ?? s.phase}
            </span>
            <div className="min-w-0 flex-1">
              <div className="truncate font-medium text-slate-200">
                {s.phase}. {s.name}
              </div>
              <div className="truncate text-[10px] text-slate-400">
                {s.status.replace('_', ' ').toLowerCase()} · gate: {s.reviewerRole}
                {s.reviewedBy ? ` · by ${s.reviewedBy.split('@')[0]}` : ''}
              </div>
            </div>
            {s.phase === currentPhase && !isSelected && (
              <span className="shrink-0 rounded bg-white/10 px-1 text-[9px] font-semibold text-slate-300">now</span>
            )}
          </Wrapper>
        );
      })}
    </div>
  );
}
