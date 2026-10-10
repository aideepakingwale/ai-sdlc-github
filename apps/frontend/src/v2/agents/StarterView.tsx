import { useMutation, useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import { ROLE_LABEL, STAGE_NAMES, type DefDetail, type DefKind, type StarterDetail } from '../../lib/agents';
import { Pill } from '../bits';

const primary = 'rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-50';

/** A built-in agent or skill a project may copy: its current definition, read-only, and the button that makes an editable copy for this project. */
export default function StarterView({ projectId, kind, coreId, canCopy, onBack, onCopied }: { projectId: string; kind: DefKind; coreId: string; canCopy: boolean; onBack: () => void; onCopied: (defId: string) => void }) {
  const q = useQuery({ queryKey: ['agent-starter', projectId, kind, coreId], queryFn: () => api.get<StarterDetail>(`/api/projects/${projectId}/agent-starters/${kind}/${coreId}`), retry: false });
  const copy = useMutation({
    mutationFn: () => api.post<DefDetail>('/api/agent-defs/fork', { sourceKind: 'core', sourceId: coreId, kind, scope: 'project', projectId }),
    onSuccess: (d) => onCopied(d.def.id),
  });
  const d = q.data;
  return (
    <div data-testid="v2-starter-view">
      <button type="button" onClick={onBack} className="text-xs font-semibold text-brand-700 hover:underline" data-testid="v2-starter-back">← Back to the library</button>
      {q.isError ? <div className="mt-2 rounded-xl border border-slate-300 bg-white p-4 text-sm text-slate-700" role="alert">{(q.error as Error).message}</div> : !d ? <div className="mt-2 animate-pulse text-sm text-slate-400">Loading…</div> : (
        <>
          <div className="mt-1 flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-2"><h3 className="font-display text-xl font-bold text-navy">{d.name}</h3><Pill>🔒 Built-in · read-only</Pill><Pill>{ROLE_LABEL[d.definition.role] ?? d.definition.role}</Pill>{d.stage != null && <Pill tone="brand">{STAGE_NAMES[d.stage] ?? `Stage ${d.stage}`}</Pill>}</div>
            {canCopy && <button type="button" className={primary} disabled={copy.isPending} data-testid="v2-starter-copy" onClick={() => copy.mutate()}>{copy.isPending ? 'Copying…' : 'Copy to project and edit'}</button>}
          </div>
          <p className="mt-1 text-sm text-slate-600">{d.description}</p>
          <p className="mt-1 text-xs text-slate-500">A copy starts from exactly this definition and is yours to change. It does not follow the built-in if that is updated, and it goes through the audit and approval like any agent you build.</p>
          <div className="mt-3 grid gap-3 md:grid-cols-[minmax(0,1fr)_18rem]">
            <div className="rounded-xl border border-slate-300 bg-white p-3"><div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Instructions</div>
              <pre className="max-h-[28rem] overflow-auto whitespace-pre-wrap font-mono text-[12.5px] leading-relaxed text-slate-800" data-testid="v2-starter-prompt">{d.definition.prompt}</pre></div>
            <div className="space-y-3 text-xs">
              <div className="rounded-xl border border-slate-300 bg-white p-3"><div className="mb-1 font-semibold uppercase tracking-wide text-slate-500">Reads</div>
                <div className="flex flex-wrap gap-1">{d.definition.inputs.map((i) => <Pill key={i.name} tone="brand">● {i.name} <span className="font-normal text-slate-500">{i.source}</span></Pill>)}</div></div>
              <div className="rounded-xl border border-slate-300 bg-white p-3"><div className="mb-1 font-semibold uppercase tracking-wide text-slate-500">Writes</div>
                <div className="flex flex-wrap gap-1">{d.definition.outputs.map((o) => <Pill key={o.name} tone="green">{o.name} → {o.artefact_type}</Pill>)}</div></div>
              <div className="rounded-xl border border-slate-300 bg-white p-3 text-slate-600">Model role <b>{ROLE_LABEL[d.definition.role]}</b>, temperature <b>{d.definition.temperature}</b>.</div>
            </div>
          </div>
        </>)}
    </div>
  );
}
