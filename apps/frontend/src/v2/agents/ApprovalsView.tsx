import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../api/client';
import { lineDiff, type DefDetail, type PendingItem } from '../../lib/agents';
import { Pill } from '../bits';

const btn = 'rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:border-brand-400 disabled:opacity-50';
const primary = 'rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-50';

/** Versions waiting for a decision. A project's list is for its approvers; the organisation list (projectId null) is for super-admins. */
export default function ApprovalsView({ projectId, userId, onOpen }: { projectId: string | null; userId: string; onOpen?: (defId: string) => void }) {
  const qc = useQueryClient();
  const url = projectId ? `/api/projects/${projectId}/agent-approvals` : '/api/agent-library/approvals';
  const q = useQuery({ queryKey: ['agent-approvals', projectId ?? 'org'], queryFn: () => api.get<{ pending: PendingItem[] }>(url) });
  const [sel, setSel] = useState<string | null>(null);
  const [comment, setComment] = useState('');
  const [msg, setMsg] = useState('');
  const items = q.data?.pending ?? [];
  const cur = items.find((i) => i.defId === sel) ?? items[0] ?? null;
  const detail = useQuery({ queryKey: ['agent-def', cur?.defId], enabled: Boolean(cur), queryFn: () => api.get<DefDetail>(`/api/agent-defs/${cur!.defId}`) });
  const decide = useMutation({
    mutationFn: (decision: string) => api.post<DefDetail>(`/api/agent-defs/${cur!.defId}/decision`, { decision, comment }),
    onSuccess: (_d, decision) => {
      setComment(''); setMsg(decision === 'approve' ? 'Approved and published.' : decision === 'changes' ? 'Sent back for changes.' : 'Rejected.');
      void qc.invalidateQueries({ queryKey: ['agent-approvals'] }); void qc.invalidateQueries({ queryKey: ['agent-lists'] }); void qc.invalidateQueries({ queryKey: ['agent-def'] });
    },
    onError: (e) => setMsg(e instanceof Error ? e.message : 'Could not record the decision'),
  });
  if (q.isError) return <div className="rounded-xl border border-slate-300 bg-white p-4 text-sm text-slate-600" role="alert">You cannot approve agents or skills here. {(q.error as Error).message}</div>;
  if (q.isLoading) return <div className="animate-pulse text-sm text-slate-400">Loading…</div>;
  if (!items.length) return <div className="rounded-xl border border-slate-300 bg-white p-6 text-center text-sm text-slate-500" data-testid="v2-approvals-empty">Nothing is waiting for approval. {msg && <span className="font-semibold text-slate-700">{msg}</span>}</div>;
  const own = cur?.submittedById != null && cur.submittedById === userId;
  const body = detail.data?.current?.body;
  const diff = cur ? lineDiff(cur.previousPrompt, body?.prompt ?? '') : [];
  const rep = detail.data?.current?.auditReport;
  return (
    <div className="grid items-start gap-3 md:grid-cols-[18rem_minmax(0,1fr)]" data-testid="v2-approvals">
      <ul className="space-y-1.5" aria-label="Waiting for approval">
        {items.map((i) => (
          <li key={i.defId}><button type="button" onClick={() => { setSel(i.defId); setMsg(''); }} data-testid="v2-approval-item" aria-current={i.defId === cur?.defId}
            className={`w-full rounded-xl border p-3 text-left text-sm ${i.defId === cur?.defId ? 'border-brand-500 bg-brand-50' : 'border-slate-300 bg-white hover:border-brand-300'}`}>
            <div className="font-semibold text-slate-800">{i.name}</div>
            <div className="text-xs text-slate-500">{i.kind} · v{i.version} · by {i.submittedBy || 'unknown'}</div>
            <div className="mt-1 flex gap-1">{(i.audit.block ?? 0) > 0 && <Pill tone="red">{i.audit.block} blocking</Pill>}{(i.audit.warn ?? 0) > 0 && <Pill tone="amber">{i.audit.warn} to review{i.acknowledged ? ' · accepted' : ''}</Pill>}{!i.audit.block && !i.audit.warn && <Pill tone="green">Audit clear</Pill>}</div>
          </button></li>
        ))}
      </ul>
      {cur && (
        <div className="rounded-xl border border-slate-300 bg-white p-4" data-testid="v2-approval-review">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div><h3 className="font-display text-lg font-bold text-navy">{cur.name} <span className="text-sm font-normal text-slate-500">v{cur.version}</span></h3>
              <div className="text-xs text-slate-500">Submitted by {cur.submittedBy}{cur.source ? ` · copied from ${cur.source}` : ''}</div></div>
            {onOpen && <button type="button" className={btn} onClick={() => onOpen(cur.defId)}>Open in builder</button>}
          </div>
          {body && <p className="mt-2 text-sm text-slate-700">{body.description}</p>}
          {body && <div className="mt-2 flex flex-wrap gap-1.5 text-xs"><Pill>{body.role}</Pill>{body.inputs.map((i) => <Pill key={i.name} tone="brand">● {i.name}</Pill>)}{body.outputs.map((o) => <Pill key={o.name} tone="green">{o.name} → {o.artefact_type}</Pill>)}{(body.children ?? []).length > 0 && <Pill tone="amber">delegates to {(body.children ?? []).length}</Pill>}</div>}
          {rep && <div className="mt-3 rounded-lg bg-slate-50 p-3 text-xs"><b>Audit report</b> · {rep.summary.block} blocking, {rep.summary.warn} to review, {rep.summary.pass} passed
            <ul className="mt-1 space-y-0.5">{rep.findings.filter((f) => f.severity !== 'pass').map((f) => <li key={f.id}><Pill tone={f.severity === 'block' ? 'red' : 'amber'}>{f.area}</Pill> {f.title}</li>)}</ul>
            {rep.probes.length > 0 && <div className="mt-1 text-slate-500">Adversarial probes held: {rep.probes.filter((p) => p.held).length} of {rep.probes.length}</div>}</div>}
          <div className="mt-3"><div className="mb-1 text-xs font-semibold text-slate-600">{cur.previousPrompt ? 'Prompt changes since the approved version' : 'Prompt'}</div>
            <pre className="max-h-64 overflow-auto rounded-lg border border-slate-200 bg-slate-50 p-2 font-mono text-xs leading-relaxed" data-testid="v2-approval-diff">
              {(cur.previousPrompt ? diff : (body?.prompt ?? '').split('\n').map((t) => ({ kind: 'same' as const, text: t }))).map((d, i) => <div key={i} className={d.kind === 'add' ? 'bg-emerald-100 text-emerald-800' : d.kind === 'del' ? 'bg-bared-200 text-bared-700 line-through' : ''}>{d.kind === 'add' ? '+ ' : d.kind === 'del' ? '- ' : '  '}{d.text}</div>)}
            </pre></div>
          {own && <div className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900" role="note" data-testid="v2-fouryes">You submitted this version, so someone else has to approve it.</div>}
          <label className="mt-3 block text-xs font-semibold text-slate-600">Comment <span className="font-normal text-slate-400">(needed when asking for changes)</span>
            <input aria-label="Decision comment" className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1 text-sm font-normal" value={comment} onChange={(e) => setComment(e.target.value)} /></label>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button type="button" className={primary} disabled={decide.isPending || own} data-testid="v2-approve" onClick={() => decide.mutate('approve')}>Approve and publish</button>
            <button type="button" className={btn} disabled={decide.isPending} data-testid="v2-changes" onClick={() => decide.mutate('changes')}>Request changes</button>
            <button type="button" className={btn} disabled={decide.isPending} data-testid="v2-reject" onClick={() => decide.mutate('reject')}>Reject</button>
            {msg && <span className="text-xs text-slate-600" role="status">{msg}</span>}
          </div>
        </div>
      )}
    </div>
  );
}
