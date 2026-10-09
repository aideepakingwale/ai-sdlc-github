import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';
import type { FlowStage, ProjectFlow } from '../api/flow';
import { COLOR_CLASSES } from '../api/flow';
import ProjectContextGraph from '../components/ProjectContextGraph';
import { formatChars, type ContextOverview } from '../lib/contextGraph';
import { Pill, StageDot, stageTone } from './bits';
import { attentionItems, groupByLevel } from './pipelineLib';

const KIND_TONE = { escalated: 'red', failed: 'red', review: 'amber', amend: 'orange', stale: 'amber' } as const;
const KIND_LABEL = { escalated: 'Escalated', failed: 'Failed', review: 'Review', amend: 'Amending', stale: 'Outdated' } as const;

/** Three colours, as in the design: what is always given, what came from earlier stages, what the user supplied. */
const BAND = (id: string): 'base' | 'upstream' | 'brief' =>
  id === 'upstream' ? 'upstream' : ['input', 'revision', 'attached', 'retrieved'].includes(id) ? 'brief' : 'base';
const BAND_COLOR = { base: 'bg-navy', upstream: 'bg-brand-300', brief: 'bg-bared-500' } as const;

function ContextBar({ phase, overview }: { phase: number; overview: ContextOverview | undefined }) {
  const c = overview?.stages.find((s) => s.phase === phase);
  if (!c) return <div className="mt-2 text-xs text-slate-400">Not run yet</div>;
  const sums = { base: 0, upstream: 0, brief: 0 };
  for (const l of c.layers) sums[BAND(l.id)] += l.tokens;
  const total = Math.max(1, sums.base + sums.upstream + sums.brief);
  return (
    <div className="mt-2">
      <div className="flex h-1.5 overflow-hidden rounded-full bg-slate-200" role="img" aria-label={`≈${formatChars(c.totals.tokens)} tokens of context`}>
        {(['base', 'upstream', 'brief'] as const).map((k) => sums[k] > 0 && <span key={k} className={BAND_COLOR[k]} style={{ width: `${(sums[k] / total) * 100}%` }} />)}
      </div>
      <div className="mt-1 text-[11px] text-slate-500">≈{formatChars(c.totals.tokens)} tokens of context</div>
    </div>
  );
}

const riskTone = (risk: string | null): 'red' | 'amber' | 'green' | 'brand' => (risk === 'HIGH' || risk === 'CRITICAL' ? 'red' : risk === 'MEDIUM' ? 'amber' : risk === 'LOW' ? 'green' : 'brand');

function StageCard({ s, overview, current, onOpen }: { s: FlowStage; overview: ContextOverview | undefined; current: boolean; onOpen: () => void }) {
  return (
    <button type="button" onClick={onOpen} data-testid={`v2-pipe-stage-${s.phase}`}
      className={`w-52 shrink-0 rounded-xl border bg-white p-3 text-left shadow-sm hover:border-brand-400 ${current ? 'border-brand-500 ring-2 ring-brand-100' : 'border-slate-200'}`}>
      <div className="flex items-start gap-2">
        <StageDot n={s.phase} color={s.color} size={22} />
        <div className="min-w-0"><div className="text-sm font-bold leading-tight text-navy">{s.name}</div>
          <div className="mt-0.5 text-xs text-slate-500">{s.persona} · reviewer {s.reviewerRole}</div></div>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        <Pill tone={stageTone(s.color)}>{COLOR_CLASSES[s.color].label}</Pill>
        <Pill>{s.artifactCount} artefact{s.artifactCount === 1 ? '' : 's'}</Pill>
        {s.stale && <Pill tone="amber">Outdated</Pill>}
        {s.security?.applies && <span data-testid={`v2-pipe-security-${s.phase}`}><Pill tone={riskTone(s.security.risk)} title="Security review at this stage">{s.security.risk ? `Security ${s.security.risk}` : 'Security on'}</Pill></span>}
      </div>
      <ContextBar phase={s.phase} overview={overview} />
    </button>
  );
}

/** The whole project at a glance, as the design shows it: levels left to right, parallel stages stacked in a level. */
export default function PipelineView({
  projectId, projectName, flow, currentPhase, onOpenStage, onDesigner, canDesign,
}: {
  projectId: string; projectName?: string; flow: ProjectFlow; currentPhase?: number; onOpenStage: (seq: number) => void; onDesigner: () => void; canDesign: boolean;
}) {
  const [graph, setGraph] = useState(false);
  const total = flow.stages.length;
  const done = flow.stages.filter((s) => s.status === 'APPROVED').length;
  const attention = attentionItems(flow.stages);
  const groups = groupByLevel(flow.stages, flow.levels);
  const overview = useQuery({
    queryKey: ['context-overview', projectId, flow.stages.map((s) => `${s.phase}:${s.status}:${s.updatedAt ?? ''}`).join('|')],
    queryFn: () => api.get<ContextOverview>(`/api/projects/${projectId}/context/overview`),
  });
  return (
    <div className="h-full overflow-y-auto bg-slate-50" data-testid="v2-pipeline">
      <div className="mx-auto max-w-[1200px] space-y-4 p-5">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="font-display text-xl font-bold text-navy">Pipeline</h1>
          <Pill tone={done === total && total > 0 ? 'green' : 'slate'}>{done} of {total} approved</Pill>
          {projectName && <Pill>{projectName}</Pill>}
          <span className="ml-auto flex gap-2">
            <button type="button" onClick={() => setGraph(true)} data-testid="v2-open-graph" className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:border-brand-400">Project context graph</button>
            {canDesign && <button type="button" onClick={onDesigner} className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:border-brand-400">Workflow designer</button>}
          </span>
        </div>

        <section className="rounded-xl border border-slate-200 bg-white p-4" aria-label="Stages and gates">
          <h2 className="text-[15px] font-semibold text-navy">Stages and gates</h2>
          <p className="mb-3 text-[13px] text-slate-500">Stages in the same level run in parallel. The next level starts when every gate in the level is approved.</p>
          <div className="overflow-x-auto pb-2">
            <ol className="flex min-w-max items-center gap-3">
              {groups.map((g, i) => (
                <li key={i} className="flex items-center gap-3">
                  <div>
                    <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Level {i + 1}{g.length > 1 ? ' · runs in parallel' : ''}</div>
                    <div className="flex flex-col gap-3">{g.map((s) => <StageCard key={s.phase} s={s} overview={overview.data} current={s.phase === currentPhase} onOpen={() => onOpenStage(s.phase)} />)}</div>
                  </div>
                  {i < groups.length - 1 && <span aria-hidden="true" className="mt-5 text-slate-400">→</span>}
                </li>
              ))}
            </ol>
          </div>
          <div className="mt-2 flex flex-wrap gap-4 text-xs text-slate-500" aria-label="Context colours">
            <span className="inline-flex items-center gap-1.5"><i className="h-2 w-2 rounded-sm bg-navy" />Instructions &amp; project</span>
            <span className="inline-flex items-center gap-1.5"><i className="h-2 w-2 rounded-sm bg-brand-300" />Earlier stages</span>
            <span className="inline-flex items-center gap-1.5"><i className="h-2 w-2 rounded-sm bg-bared-500" />Your brief &amp; attachments</span>
          </div>
        </section>

        <section className="rounded-xl border border-slate-200 bg-white p-4" aria-label="Needs your attention" data-testid="v2-attention">
          <h2 className="text-[15px] font-semibold text-navy">Needs your attention</h2>
          <p className="mb-3 text-[13px] text-slate-500">Gates waiting for a reviewer, stages to re-plan, outdated outputs.</p>
          {attention.length === 0 ? (
            <div className="rounded-lg bg-slate-100 px-4 py-3 text-sm text-slate-500">Nothing is waiting on anyone.</div>
          ) : (
            <ul className="space-y-1.5">
              {attention.map((a, i) => (
                <li key={`${a.phase}-${a.kind}-${i}`}>
                  <button type="button" onClick={() => onOpenStage(a.phase)} className="flex w-full items-center gap-3 rounded-lg border border-slate-200 px-3 py-2 text-left hover:border-brand-400">
                    <Pill tone={KIND_TONE[a.kind]}>Stage {a.phase}</Pill>
                    <span className="min-w-0 flex-1 truncate text-sm text-slate-800"><b>{KIND_LABEL[a.kind]}</b> · {a.name} — {a.text}</span>
                    <span className="text-sm text-brand-600">Open</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
      {graph && <ProjectContextGraph projectId={projectId} onClose={() => setGraph(false)} onOpenStage={(p) => { setGraph(false); onOpenStage(p); }} />}
    </div>
  );
}
