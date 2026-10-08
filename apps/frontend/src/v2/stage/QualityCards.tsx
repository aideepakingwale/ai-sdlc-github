import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../api/client';
import { Pill } from '../bits';

export interface Feedback {
  id: string; phase: number; artefactId: string | null; source: 'validation' | 'human' | 'security'; rating: number | null; category: string;
  severity: 'error' | 'warning' | 'info'; comment: string; status: 'open' | 'resolved'; createdBy: string | null; createdAt: string; resolvedBy: string | null;
}

/** The stage's quality and security findings; the dock reads the blocking count from the same query. */
export function useFeedback(projectId: string, phase: number) {
  const key = ['feedback', projectId, phase];
  const q = useQuery({ queryKey: key, queryFn: () => api.get<{ feedback: Feedback[] }>(`/api/projects/${projectId}/phase/${phase}/feedback`), enabled: Boolean(projectId) });
  const items = q.data?.feedback ?? [];
  return {
    key, items,
    validation: items.filter((f) => f.source === 'validation'),
    security: items.filter((f) => f.source === 'security'),
    human: items.filter((f) => f.source === 'human'),
    openBlocking: items.filter((f) => f.source === 'security' && f.status === 'open' && f.category === 'security-critical').length,
  };
}

const STRIPE = { error: 'border-l-bared-500 bg-bared-200/40', warning: 'border-l-amber-500 bg-amber-50', info: 'border-l-slate-300 bg-slate-50' } as const;
const CATEGORIES = ['quality', 'accuracy', 'completeness', 'hallucination', 'syntax', 'intent', 'other'] as const;

function Finding({ f, canResolve, onResolve, busy, id, action }: { f: Feedback; canResolve: boolean; onResolve: (id: string) => void; busy: boolean; id?: string; action?: React.ReactNode }) {
  const resolved = f.status === 'resolved';
  const label = f.category.replace('security-', '');
  return (
    <div id={id} className={`rounded-lg border border-slate-200 border-l-4 px-3 py-2 ${resolved ? 'border-l-slate-300 bg-slate-50 opacity-70' : STRIPE[f.severity]}`}>
      <div className="flex items-center gap-2 text-xs font-semibold text-slate-700">
        <span>{label}{f.source === 'validation' ? ' · validation agent' : f.source === 'human' ? ' · reported' : ''}</span>
        {resolved && <Pill tone="green">resolved</Pill>}
        {!resolved && canResolve && f.category !== 'security-rating' && f.source !== 'validation' && (
          <button type="button" onClick={() => onResolve(f.id)} disabled={busy} className="ml-auto rounded-lg border border-slate-300 bg-white px-2.5 py-0.5 text-xs font-semibold hover:border-brand-400">Resolve</button>
        )}
      </div>
      <p className="mt-0.5 text-sm text-slate-800">{f.comment}</p>
      {action}
    </div>
  );
}

/** Security review and quality checks, as the design shows them: severity stripes, a risk chip, Resolve on each finding. */
export default function QualityCards({ projectId, phase, canResolve, onUploadMissing, uploading }: {
  projectId: string; phase: number; canResolve: boolean; onUploadMissing?: () => void; uploading?: boolean;
}) {
  const qc = useQueryClient();
  const { key, validation, security, human, openBlocking } = useFeedback(projectId, phase);
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState<(typeof CATEGORIES)[number]>('quality');
  const [severity, setSeverity] = useState<Feedback['severity']>('warning');
  const [comment, setComment] = useState('');
  const refresh = () => void qc.invalidateQueries({ queryKey: key });
  const report = useMutation({ mutationFn: (b: { category: string; severity: string; comment: string }) => api.post(`/api/projects/${projectId}/phase/${phase}/feedback`, b), onSuccess: () => { setComment(''); setOpen(false); refresh(); } });
  const resolve = useMutation({ mutationFn: (id: string) => api.post(`/api/projects/${projectId}/feedback/${id}/resolve`), onSuccess: refresh, onError: (e) => window.alert(e instanceof Error ? e.message : 'Could not resolve') });
  const risk = openBlocking ? ['HIGH risk', 'red'] as const : security.some((f) => f.status === 'open' && f.severity === 'warning') ? ['MEDIUM risk', 'amber'] as const : ['LOW risk', 'green'] as const;
  const quality = [...validation, ...human];
  return (
    <>
      {security.length > 0 && (
        <section className="rounded-xl border border-slate-200 bg-white p-4" id="v2-checks" data-testid="security-review">
          <div className="flex items-start justify-between gap-3"><h2 className="text-[15px] font-semibold text-navy">Security review</h2><Pill tone={risk[1]}>{risk[0]}</Pill></div>
          <p className="mb-3 mt-0.5 text-[13px] text-slate-500">Run automatically when the output reached review. Critical findings block approval until they are resolved.</p>
          <div className="space-y-2">{security.map((f) => <Finding key={f.id} f={f} canResolve={canResolve} onResolve={(id) => resolve.mutate(id)} busy={resolve.isPending} />)}</div>
        </section>
      )}
      <section className="rounded-xl border border-slate-200 bg-white p-4" data-testid="quality-checks">
        <div className="flex items-start justify-between gap-3">
          <h2 className="text-[15px] font-semibold text-navy">Quality checks</h2>
          <button type="button" onClick={() => setOpen((v) => !v)} className="rounded-lg border border-slate-300 px-3 py-1 text-xs font-semibold hover:border-brand-400">{open ? 'Cancel' : 'Report an issue'}</button>
        </div>
        <p className="mb-3 mt-0.5 text-[13px] text-slate-500">{validation.length === 0 ? 'The validation agent found no issues.' : `The validation agent flagged ${validation.length}.`} Use Report an issue to flag anything else.</p>
        {open && (
          <form className="mb-3 rounded-lg border border-slate-200 bg-slate-50 p-3" onSubmit={(e) => { e.preventDefault(); report.mutate({ category, severity, comment }); }}>
            <div className="flex flex-wrap gap-2">
              <select value={category} onChange={(e) => setCategory(e.target.value as (typeof CATEGORIES)[number])} aria-label="Issue category" className="rounded-lg border border-slate-300 bg-white px-2 py-1 text-sm">{CATEGORIES.map((c) => <option key={c}>{c}</option>)}</select>
              <select value={severity} onChange={(e) => setSeverity(e.target.value as Feedback['severity'])} aria-label="Issue severity" className="rounded-lg border border-slate-300 bg-white px-2 py-1 text-sm"><option value="error">error</option><option value="warning">warning</option><option value="info">info</option></select>
            </div>
            <textarea value={comment} onChange={(e) => setComment(e.target.value)} rows={2} placeholder="What is wrong and what did you expect?" className="mt-2 w-full rounded-lg border border-slate-300 bg-white p-2 text-sm focus:border-brand-500 focus:outline-none" />
            <button disabled={report.isPending || !comment.trim()} className="mt-2 rounded-lg bg-brand-600 px-3 py-1 text-sm font-semibold text-white disabled:opacity-40">{report.isPending ? 'Submitting…' : 'Submit report'}</button>
          </form>
        )}
        <div className="space-y-2">
          {quality.map((f) => (
            <Finding key={f.id} f={f} canResolve={canResolve} onResolve={(id) => resolve.mutate(id)} busy={resolve.isPending}
              action={f.category === 'missing-document' && onUploadMissing ? <button type="button" onClick={onUploadMissing} disabled={uploading} className="mt-1.5 rounded-lg border border-slate-300 bg-white px-2.5 py-0.5 text-xs font-semibold disabled:opacity-40">📎 Upload the missing document, then regenerate</button> : undefined} />
          ))}
        </div>
      </section>
    </>
  );
}
