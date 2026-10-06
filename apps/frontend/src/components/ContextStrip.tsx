import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';
import type { FlowStage } from '../api/flow';
import ProjectContextGraph from './ProjectContextGraph';
import { LAYER_COLORS, formatChars, type ContextOverview } from '../lib/contextGraph';

/**
 * Pipeline-level view of context: for every stage, how much its latest run was given and where it came
 * from (one bar per stage, coloured by layer). Click a stage to open it. Read-only; anyone who can see
 * the pipeline can see it.
 */
export default function ContextStrip({ projectId, stages, focusedPhase, onFocusPhase }: {
  projectId: string; stages: FlowStage[]; focusedPhase: number | null; onFocusPhase: (p: number | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [graph, setGraph] = useState(false);
  const q = useQuery({
    queryKey: ['context-overview', projectId, stages.map((s) => `${s.phase}:${s.status}:${s.updatedAt ?? ''}`).join('|')],
    queryFn: () => api.get<ContextOverview>(`/api/projects/${projectId}/context/overview`),
    enabled: open,
  });
  const byPhase = new Map((q.data?.stages ?? []).map((s) => [s.phase, s]));
  const max = Math.max(1, ...(q.data?.stages ?? []).map((s) => s.totals.tokens));

  return (
    <div className="mt-2 border-t border-slate-100 pt-2" data-testid="context-strip">
      <div className="flex items-center gap-3">
        <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} className="text-[11px] font-semibold text-slate-500 hover:text-brand-700">
          {open ? '▾' : '▸'} Context by stage
        </button>
        <button type="button" onClick={() => setGraph(true)} className="rounded border border-slate-200 px-1.5 py-0.5 text-[11px] font-semibold text-slate-600 hover:border-brand-300 hover:text-brand-700"
          title="See how context flows through the whole project">◎ Project context graph</button>
      </div>
      {graph && <ProjectContextGraph projectId={projectId} onClose={() => setGraph(false)} onOpenStage={(p) => onFocusPhase(p)} />}
      {open && (
        <div className="mt-1.5 space-y-1">
          {q.isLoading && <div className="text-[11px] text-slate-400">Loading…</div>}
          {stages.map((s) => {
            const c = byPhase.get(s.phase);
            return (
              <button key={s.phase} type="button" onClick={() => onFocusPhase(focusedPhase === s.phase ? null : s.phase)}
                className={`flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-[11px] hover:bg-slate-50 ${focusedPhase === s.phase ? 'bg-brand-50' : ''}`}>
                <span className="w-32 shrink-0 truncate font-semibold text-slate-600" title={s.name}>{s.phase}. {s.name}</span>
                {c ? (
                  <>
                    <span className="flex h-2 flex-1 overflow-hidden rounded-full bg-slate-100" style={{ maxWidth: `${Math.max(12, (c.totals.tokens / max) * 100)}%` }}
                      role="img" aria-label={`${s.name}: ≈${formatChars(c.totals.tokens)} tokens of context`}>
                      {c.layers.filter((l) => l.tokens > 0).map((l) => (
                        <span key={l.id} title={`${l.label}: ≈${formatChars(l.tokens)} tokens, ${l.items} item(s)`}
                          style={{ width: `${(l.tokens / Math.max(1, c.totals.tokens)) * 100}%`, background: LAYER_COLORS[l.id] }} />
                      ))}
                    </span>
                    <span className="shrink-0 text-slate-400">≈{formatChars(c.totals.tokens)} tok{c.totals.condensed ? ` · ${c.totals.condensed} condensed` : ''}</span>
                  </>
                ) : <span className="text-slate-300">not run yet</span>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
