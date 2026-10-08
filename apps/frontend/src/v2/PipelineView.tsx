import { useState } from 'react';
import type { ProjectFlow } from '../api/flow';
import { COLOR_CLASSES } from '../api/flow';
import ContextStrip from '../components/ContextStrip';
import ProjectContextGraph from '../components/ProjectContextGraph';
import { Pill, StageDot, stageTone } from './bits';
import { attentionItems, groupByLevel } from './pipelineLib';

const KIND_TONE = { escalated: 'red', failed: 'red', review: 'amber', amend: 'orange', stale: 'amber' } as const;

/** The whole project at a glance: what needs a person, then the stages by step (parallel ones side by side). */
export default function PipelineView({
  projectId, flow, onOpenStage, onDesigner, canDesign,
}: {
  projectId: string; flow: ProjectFlow; onOpenStage: (seq: number) => void; onDesigner: () => void; canDesign: boolean;
}) {
  const [graph, setGraph] = useState(false);
  const total = flow.stages.length;
  const done = flow.stages.filter((s) => s.status === 'APPROVED').length;
  const attention = attentionItems(flow.stages);
  const groups = groupByLevel(flow.stages, flow.levels);
  return (
    <div className="h-full overflow-y-auto" data-testid="v2-pipeline">
      <div className="mx-auto max-w-5xl space-y-5 p-6">
        <div className="flex flex-wrap items-center gap-3">
          <div>
            <h1 className="text-lg font-bold text-navy">Pipeline</h1>
            <p className="text-sm text-slate-500">{done} of {total} stages approved · workflow v{flow.workflowVersion}</p>
          </div>
          <div className="ml-auto flex gap-2">
            <button type="button" onClick={() => setGraph(true)} className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:border-brand-400" data-testid="v2-open-graph">◎ Project context graph</button>
            {canDesign && <button type="button" onClick={onDesigner} className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:border-brand-400">Edit workflow</button>}
          </div>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-slate-200" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={done}>
          <div className="h-full bg-emerald-600 transition-all" style={{ width: `${total ? (done / total) * 100 : 0}%` }} />
        </div>

        <section aria-label="Needs attention" data-testid="v2-attention">
          <h2 className="mb-2 text-sm font-semibold text-navy">Needs attention</h2>
          {attention.length === 0 ? (
            <div className="rounded-lg bg-slate-100 px-4 py-3 text-sm text-slate-500">Nothing is waiting on anyone.</div>
          ) : (
            <ul className="space-y-1.5">
              {attention.map((a, i) => (
                <li key={`${a.phase}-${a.kind}-${i}`}>
                  <button type="button" onClick={() => onOpenStage(a.phase)} className="flex w-full items-center gap-3 rounded-lg border border-slate-200 bg-white px-3 py-2 text-left hover:border-brand-400">
                    <Pill tone={KIND_TONE[a.kind]}>{a.kind === 'review' ? 'Review' : a.kind === 'stale' ? 'Outdated' : a.kind === 'amend' ? 'Amending' : a.kind === 'failed' ? 'Failed' : 'Escalated'}</Pill>
                    <span className="min-w-0 flex-1 truncate text-sm text-slate-800"><b>Stage {a.phase} · {a.name}</b> — {a.text}</span>
                    <span className="text-xs text-brand-600">Open →</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section aria-label="Stages by step">
          <h2 className="mb-2 text-sm font-semibold text-navy">Stages</h2>
          <ol className="space-y-3">
            {groups.map((g, i) => (
              <li key={i} className="flex gap-3">
                <div className="w-14 shrink-0 pt-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Step {i + 1}{g.length > 1 && <div className="mt-0.5 normal-case text-[10px] font-medium text-brand-600">parallel</div>}</div>
                <div className={`grid min-w-0 flex-1 gap-3 ${g.length > 1 ? "sm:grid-cols-2" : ""}`}>
                  {g.map((s) => (
                    <button key={s.phase} type="button" onClick={() => onOpenStage(s.phase)} data-testid={`v2-pipe-stage-${s.phase}`}
                      className="flex items-start gap-3 rounded-xl border border-slate-200 bg-white p-3 text-left shadow-sm hover:border-brand-400">
                      <StageDot n={s.phase} color={s.color} size={28} />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-semibold text-navy">{s.name}</span>
                        <span className="mt-0.5 block text-xs text-slate-500">{s.persona} · sign-off {s.reviewerRole}</span>
                        <span className="mt-1.5 flex flex-wrap items-center gap-1.5">
                          <Pill tone={stageTone(s.color)}>{COLOR_CLASSES[s.color].label}</Pill>
                          {s.stale && <Pill tone="amber">Outdated</Pill>}
                          <span className="text-xs text-slate-500">{s.artifactCount} artefact{s.artifactCount === 1 ? '' : 's'}{s.assignee ? ` · ${s.assignee.displayName}` : ''}</span>
                        </span>
                      </span>
                    </button>
                  ))}
                </div>
              </li>
            ))}
          </ol>
        </section>

        <section aria-label="Context across the pipeline">
          <h2 className="mb-1 text-sm font-semibold text-navy">Context across the pipeline</h2>
          <p className="mb-1 text-xs text-slate-500">How much each stage was given and where it came from.</p>
          <ContextStrip projectId={projectId} stages={flow.stages} focusedPhase={null} onFocusPhase={(p) => { if (p != null) onOpenStage(p); }} />
        </section>
      </div>
      {graph && <ProjectContextGraph projectId={projectId} onClose={() => setGraph(false)} onOpenStage={(p) => { setGraph(false); onOpenStage(p); }} />}
    </div>
  );
}
