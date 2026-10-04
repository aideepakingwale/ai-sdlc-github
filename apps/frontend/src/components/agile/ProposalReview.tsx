import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { agileApi, type Proposal, type RefineOp } from '../../api/agile';
import { capacityMeter } from '../../lib/agileView';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Callout } from '../ui/Callout';
import { Icon } from '../ui/Icon';
import { errorText, useAgileMutation } from './hooks';

const KIND = { refine: 'refine', plan: 'plan', build: 'delta' } as const;
const TITLE = { refine: 'Proposed backlog changes', plan: 'Proposed sprint scope', build: 'Proposed design changes' } as const;

/**
 * What the AI wants to change, shown BEFORE anything changes. Nothing is applied until this stage's gate is
 * approved; unticking an item removes it from the proposal, and the server re-checks everything you leave in.
 */
export default function ProposalReview({ projectId, phase, role, canEdit }: {
  projectId: string; phase: number; role: 'refine' | 'plan' | 'build'; canEdit: boolean;
}) {
  const q = useQuery({
    queryKey: ['agile-proposal', projectId, phase, role],
    queryFn: () => agileApi.proposal(projectId, phase, KIND[role]),
    refetchInterval: 6_000,
  });
  const p = q.data?.proposal ?? null;
  const [removed, setRemoved] = useState<Set<string>>(new Set());
  useEffect(() => setRemoved(new Set()), [p?.id, p?.version]);
  const save = useAgileMutation(projectId, (next: unknown) => agileApi.editProposal(projectId, p!.id, next, p!.version));
  if (!p) return null;

  const editable = canEdit && p.status === 'proposed';
  const toggle = (k: string) => setRemoved((s) => { const n = new Set(s); if (n.has(k)) n.delete(k); else n.add(k); return n; });
  const apply = () => {
    const base = p.payload as Record<string, unknown>;
    const next = role === 'refine' ? { ...base, ops: (p.payload.ops ?? []).filter((o) => !removed.has(o.ref)) }
      : role === 'plan' ? { ...base, items: (p.payload.items ?? []).filter((i) => !removed.has(i.key)) }
      : { ...base, changes: (p.payload.changes ?? []).filter((c) => !removed.has(`${c.component}/${c.section}`)) };
    save.mutate(next, { onSuccess: () => void q.refetch() });
  };

  return (
    <section className="rounded-xl border border-brand-200 bg-white" aria-label={TITLE[role]}>
      <header className="flex flex-wrap items-center gap-2 border-b border-brand-100 bg-brand-50/60 px-4 py-2.5">
        <Icon name="sparkles" size={15} className="text-brand-600" />
        <h3 className="text-sm font-semibold text-slate-800">{TITLE[role]}</h3>
        <Badge tone={p.status === 'applied' ? 'success' : 'info'}>{p.status === 'applied' ? 'Applied' : 'Not applied yet'}</Badge>
        {p.status === 'proposed' && <span className="ml-auto text-xs text-slate-500">Applied when you approve this stage</span>}
      </header>
      <div className="space-y-3 p-4">
        {p.warnings.length > 0 && (
          <Callout tone="advice" compact title="Needs your attention">
            <ul className="list-disc pl-4">{p.warnings.slice(0, 8).map((w) => <li key={w}>{w}</li>)}</ul>
          </Callout>)}
        {role === 'refine' && <RefineList ops={p.payload.ops ?? []} removed={removed} toggle={toggle} editable={editable} summary={p.payload.summary} />}
        {role === 'plan' && <PlanList p={p} removed={removed} toggle={toggle} editable={editable} />}
        {role === 'build' && <DeltaList changes={p.payload.changes ?? []} removed={removed} toggle={toggle} editable={editable} summary={p.payload.summary} />}
        {save.isError && <Callout tone="error" compact title="Not saved">{errorText(save.error)}</Callout>}
        {editable && removed.size > 0 && (
          <div className="flex items-center gap-2">
            <Button variant="primary" size="sm" icon="check" loading={save.isPending} onClick={apply}>Remove {removed.size} and re-check</Button>
            <Button variant="ghost" size="sm" onClick={() => setRemoved(new Set())}>Undo</Button>
          </div>)}
      </div>
    </section>
  );
}

function Check({ on, editable, onChange, label }: { on: boolean; editable: boolean; onChange: () => void; label: string }) {
  return <input type="checkbox" aria-label={label} className="mt-1 h-4 w-4 accent-brand-600" checked={on} disabled={!editable} onChange={onChange} />;
}

function RefineList({ ops, removed, toggle, editable, summary }: { ops: RefineOp[]; removed: Set<string>; toggle: (k: string) => void; editable: boolean; summary?: string }) {
  if (ops.length === 0) return <p className="text-sm text-slate-500">{summary || 'No backlog changes were proposed.'}</p>;
  return (
    <ul className="space-y-1.5">
      {ops.map((o) => (
        <li key={o.ref} className={`flex gap-2.5 rounded-lg border px-3 py-2 ${removed.has(o.ref) ? 'border-slate-200 bg-slate-50 opacity-50' : 'border-slate-200'}`}>
          <Check on={!removed.has(o.ref)} editable={editable} onChange={() => toggle(o.ref)} label={`Include ${o.title || o.target}`} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2 text-sm font-medium text-slate-800">
              <Badge tone={o.op === 'drop' ? 'error' : o.op === 'update' ? 'warning' : 'success'}>{o.op === 'create' ? `new ${o.type}` : `${o.op} ${o.target}`}</Badge>
              {o.title}
              {o.estimate !== null && o.estimate !== undefined && <Badge>{o.estimate} pts</Badge>}
            </div>
            {o.acceptanceCriteria.length > 0 && <ul className="mt-1 list-disc pl-5 text-xs text-slate-600">{o.acceptanceCriteria.map((c) => <li key={c}>{c}</li>)}</ul>}
            {o.rationale && <p className="mt-1 text-xs italic text-slate-500">{o.rationale}</p>}
          </div>
        </li>))}
    </ul>
  );
}

function PlanList({ p, removed, toggle, editable }: { p: Proposal; removed: Set<string>; toggle: (k: string) => void; editable: boolean }) {
  const items = p.payload.items ?? [];
  const kept = items.filter((i) => !removed.has(i.key)).reduce((n, i) => n + i.estimate, 0);
  const meter = capacityMeter(kept, p.payload.capacity ?? 0);
  return (
    <div className="space-y-2">
      {p.payload.goal && <p className="text-sm text-slate-700"><span className="font-semibold">Sprint goal:</span> {p.payload.goal}</p>}
      <Callout tone={meter.tone} compact title={meter.label} />
      {items.length === 0 ? <p className="text-sm text-slate-500">No ready items fit this sprint. Mark items ready in the backlog first.</p> : (
        <ul className="space-y-1.5">
          {items.map((i) => (
            <li key={i.key} className={`flex gap-2.5 rounded-lg border px-3 py-2 ${removed.has(i.key) ? 'border-slate-200 bg-slate-50 opacity-50' : 'border-slate-200'}`}>
              <Check on={!removed.has(i.key)} editable={editable} onChange={() => toggle(i.key)} label={`Include ${i.title}`} />
              <div className="min-w-0 flex-1 text-sm"><span className="font-mono text-[11px] text-slate-400">{i.key}</span> <span className="font-medium text-slate-800">{i.title}</span>
                {i.reason && <div className="text-xs italic text-slate-500">{i.reason}</div>}</div>
              <Badge>{i.estimate} pts</Badge>
            </li>))}
        </ul>)}
      {(p.payload.risks?.length ?? 0) > 0 && <Callout tone="warning" compact title="Risks"><ul className="list-disc pl-4">{p.payload.risks!.map((r) => <li key={r}>{r}</li>)}</ul></Callout>}
    </div>
  );
}

function DeltaList({ changes, removed, toggle, editable, summary }: {
  changes: NonNullable<Proposal['payload']['changes']>; removed: Set<string>; toggle: (k: string) => void; editable: boolean; summary?: string;
}) {
  if (changes.length === 0) return <p className="text-sm text-slate-500">{summary || 'No design changes this sprint.'}</p>;
  return (
    <ul className="space-y-1.5">
      {changes.map((c) => {
        const k = `${c.component}/${c.section}`;
        return (
          <li key={k} className={`flex gap-2.5 rounded-lg border px-3 py-2 ${removed.has(k) ? 'border-slate-200 bg-slate-50 opacity-50' : 'border-slate-200'}`}>
            <Check on={!removed.has(k)} editable={editable} onChange={() => toggle(k)} label={`Include ${k}`} />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2 text-sm font-medium text-slate-800">
                <Badge tone={c.op === 'remove' ? 'error' : c.op === 'add' ? 'success' : 'warning'}>{c.op}</Badge>{c.component} <Icon name="chevron-right" size={12} /> {c.section}
              </div>
              {c.rationale && <p className="text-xs italic text-slate-500">{c.rationale}</p>}
              {c.content && <details className="mt-1"><summary className="cursor-pointer text-xs text-brand-700">Show the new text</summary>
                <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-2 text-xs text-slate-700">{c.content}</pre></details>}
            </div>
          </li>);
      })}
    </ul>
  );
}
