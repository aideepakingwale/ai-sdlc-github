import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { COLOR_CLASSES, type FlowStage, type ProjectFlow } from '../api/flow';
import WorkflowDesigner from './WorkflowDesigner';

/**
 * Visual, color-coded pipeline flow: six stages left→right with state
 * colors, the assigned team member per stage, and a per-stage Retrigger control
 * gated server-side (stage owner / managing PM / super-admin).
 */
export default function PipelineFlow({
  projectId,
  focusedPhase,
  onFocusPhase,
  autoOpenDesigner = false,
  onDesignerAutoOpened,
}: {
  projectId: string;
  focusedPhase: number | null;
  onFocusPhase: (phase: number | null) => void;
  /** Open the Workflow Designer once on mount (e.g. right after project creation). */
  autoOpenDesigner?: boolean;
  onDesignerAutoOpened?: () => void;
}) {
  const qc = useQueryClient();
  const [designerOpen, setDesignerOpen] = useState(false);

  useEffect(() => {
    if (autoOpenDesigner) {
      setDesignerOpen(true);
      onDesignerAutoOpened?.();  // clear the one-shot so it never reopens
    }
  }, [autoOpenDesigner, onDesignerAutoOpened]);
  const flow = useQuery({
    queryKey: ['flow', projectId],
    queryFn: () => api.get<ProjectFlow>(`/api/projects/${projectId}/flow`),
    refetchInterval: 5_000,
  });

  const retrigger = useMutation({
    mutationFn: (phase: number) => api.post(`/api/projects/${projectId}/phase/${phase}/retrigger`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['flow', projectId] });
      void qc.invalidateQueries({ queryKey: ['project', projectId] });
      void qc.invalidateQueries({ queryKey: ['artefacts', projectId] });
    },
  });

  if (!flow.data) return null;

  return (
    <div className="bg-white px-4 py-3">
      {/* The pane title lives in the Panel chrome — this row carries the
          workflow version, the status legend and the designer entry point. */}
      <div className="mb-2 flex items-center gap-2 text-xs text-slate-500">
        <span className="rounded bg-slate-100 px-1.5 text-[10px]">wf v{flow.data.workflowVersion}</span>
        <span className="text-slate-300">·</span>
        {(['emerald', 'blue', 'amber', 'orange', 'slate', 'red'] as const).map((c) => (
          <span key={c} className="flex items-center gap-1">
            <span className={`h-2 w-2 rounded-full ${COLOR_CLASSES[c].dot}`} />
            {COLOR_CLASSES[c].label}
          </span>
        ))}
        {flow.data.isManager && (
          <button
            onClick={() => setDesignerOpen(true)}
            className="ml-auto rounded-lg border border-slate-200 px-2 py-1 text-[11px] font-semibold text-slate-600 hover:border-brand-300 hover:text-brand-700"
          >
            ⚙ Configure flow
          </button>
        )}
      </div>

      {/* Columns = derived execution levels; stacked cards run in PARALLEL. */}
      <div className="flex items-start gap-1 overflow-x-auto pb-1">
        {flow.data.levels.map((seqs, li) => (
          <div key={li} className="flex items-center gap-1">
            <div className="flex flex-col gap-1.5">
              {seqs.map((seq) => {
                const s = flow.data.stages.find((st) => st.phase === seq);
                return s ? (
                  <StageCard
                    key={seq}
                    s={s}
                    focused={focusedPhase === s.phase}
                    onFocus={() => onFocusPhase(focusedPhase === s.phase ? null : s.phase)}
                    onRetrigger={() => retrigger.mutate(s.phase)}
                    retriggerBusy={retrigger.isPending}
                  />
                ) : null;
              })}
              {seqs.length > 1 && (
                <div className="text-center text-[10px] font-semibold text-indigo-500">∥ parallel group</div>
              )}
            </div>
            {li < flow.data.levels.length - 1 && <div className="h-0.5 w-3 shrink-0 bg-brand-300" />}
          </div>
        ))}
      </div>
      {retrigger.isError && (
        <div className="mt-1 rounded bg-red-50 px-2 py-1 text-[11px] text-red-700">
          {retrigger.error instanceof Error ? retrigger.error.message : 'Retrigger failed'}
        </div>
      )}
      {designerOpen && <WorkflowDesigner projectId={projectId} onClose={() => setDesignerOpen(false)} />}
    </div>
  );
}

function StageCard({
  s, focused, onFocus, onRetrigger, retriggerBusy,
}: {
  s: FlowStage;
  focused: boolean;
  onFocus: () => void;
  onRetrigger: () => void;
  retriggerBusy: boolean;
}) {
  const c = COLOR_CLASSES[s.color];
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onFocus}
      onKeyDown={(e) => e.key === 'Enter' && onFocus()}
      title={`in: ${s.inputs.join(', ')} → out: ${s.outputs.join(', ')}`}
      className={`w-40 shrink-0 cursor-pointer rounded-lg border-2 bg-white p-2 transition hover:shadow-md ${
        focused ? 'border-brand-500 ring-2 ring-brand-200' : s.stale ? 'border-bared-500/50' : c.ring
      }`}
    >
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-bold text-slate-700">
          {s.phase}. {s.reviewerRole}
        </span>
        <span className={`h-2.5 w-2.5 rounded-full ${c.dot}`} title={s.status} />
      </div>
      {s.stale && (
        <div className="mt-1 rounded bg-bared-200 px-1.5 py-0.5 text-[9px] font-semibold text-bared-700" title={s.staleReason ?? 'An upstream input changed'}>
          ⚠ outdated — re-run
        </div>
      )}
      <div className="mt-0.5 truncate text-[11px] text-slate-500" title={s.name}>
        {s.name}
      </div>
      <div className={`mt-1 inline-block rounded px-1.5 py-0.5 text-[10px] font-semibold ${c.chip}`}>
        {c.label}
      </div>
      <div className="mt-1 flex items-center gap-1" title={s.assignee?.email ?? 'unassigned'}>
        <span className="flex h-4 w-4 items-center justify-center rounded-full bg-brand-100 text-[8px] font-bold text-brand-700">
          {s.assignee ? s.assignee.displayName.slice(0, 2).toUpperCase() : '—'}
        </span>
        <span className="truncate text-[10px] text-slate-500">
          {s.assignee ? s.assignee.displayName : 'unassigned'}
        </span>
        {s.team.length > 1 && <span className="text-[9px] text-slate-400">+{s.team.length - 1}</span>}
      </div>
      <div className="mt-1 flex items-center justify-between">
        <span className="text-[10px] text-slate-400">{s.artifactCount} artifacts</span>
        {s.canRetrigger && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onRetrigger();
            }}
            disabled={retriggerBusy}
            className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold text-slate-600 hover:bg-brand-100 hover:text-brand-700 disabled:opacity-40"
            title="Re-run this stage's agent (regenerates its artifacts)"
          >
            ↻ retrigger
          </button>
        )}
      </div>
      {s.reviewedBy && (
        <div className="mt-0.5 truncate text-[9px] text-emerald-600" title={s.reviewedBy}>
          ✓ {s.reviewedBy.split('@')[0]}
        </div>
      )}
    </div>
  );
}
