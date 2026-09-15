import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';
import type { User } from '../api/types';

// quality signals on a stage generation — the validation agent's
// verdict (source=validation) plus human-reported issues (source=human), with a
// report form and a resolve action. One quality view a reviewer sees before
// sign-off.
interface Feedback {
  id: string;
  phase: number;
  artefactId: string | null;
  source: 'validation' | 'human';
  rating: number | null;
  category: string;
  severity: 'error' | 'warning' | 'info';
  comment: string;
  status: 'open' | 'resolved';
  createdBy: string | null;
  createdAt: string;
  resolvedBy: string | null;
}

const SEV_META: Record<Feedback['severity'], { cls: string; icon: string }> = {
  error: { cls: 'bg-red-100 text-red-700 border-red-200', icon: '⛔' },
  warning: { cls: 'bg-amber-100 text-amber-700 border-amber-200', icon: '⚠' },
  info: { cls: 'bg-slate-100 text-slate-600 border-slate-200', icon: 'ℹ' },
};

const CATEGORIES = [
  'quality', 'accuracy', 'completeness', 'hallucination', 'syntax', 'intent', 'other',
] as const;

export default function FeedbackPanel({
  projectId, phase, canResolve,
}: {
  projectId: string;
  phase: number;
  user: User;
  canResolve: boolean;
}) {
  const qc = useQueryClient();
  const key = ['feedback', projectId, phase];
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState<(typeof CATEGORIES)[number]>('quality');
  const [severity, setSeverity] = useState<Feedback['severity']>('warning');
  const [comment, setComment] = useState('');

  const q = useQuery({
    queryKey: key,
    queryFn: () => api.get<{ feedback: Feedback[] }>(`/api/projects/${projectId}/phase/${phase}/feedback`),
    enabled: Boolean(projectId),
  });
  const items = q.data?.feedback ?? [];
  const validation = items.filter((f) => f.source === 'validation');
  const human = items.filter((f) => f.source === 'human');
  const openHuman = human.filter((f) => f.status === 'open');

  const report = useMutation({
    mutationFn: (body: { category: string; severity: string; comment: string }) =>
      api.post(`/api/projects/${projectId}/phase/${phase}/feedback`, body),
    onSuccess: () => {
      setComment(''); setOpen(false);
      void qc.invalidateQueries({ queryKey: key });
    },
  });

  const resolve = useMutation({
    mutationFn: (id: string) => api.post(`/api/projects/${projectId}/feedback/${id}/resolve`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: key }),
    onError: (e) => window.alert(e instanceof Error ? e.message : 'Could not resolve'),
  });

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">Quality &amp; feedback</span>
        {validation.length === 0 ? (
          <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700">
            ✓ Validation agent: no issues
          </span>
        ) : (
          <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-700">
            ⚠ Validation agent flagged {validation.length}
          </span>
        )}
        {openHuman.length > 0 && (
          <span className="rounded-full bg-orange-50 px-2 py-0.5 text-[11px] font-semibold text-orange-700">
            🚩 {openHuman.length} open report{openHuman.length > 1 ? 's' : ''}
          </span>
        )}
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="ml-auto rounded-lg border border-slate-300 px-2.5 py-1 text-[11px] font-semibold text-slate-600 hover:border-brand-400 hover:text-brand-700"
        >
          {open ? 'Cancel' : '🚩 Report an issue'}
        </button>
      </div>

      {/* validation agent verdict (auto) */}
      {validation.length > 0 && (
        <div className="mb-2 space-y-1">
          {validation.map((f) => {
            const s = SEV_META[f.severity];
            return (
              <div key={f.id} className={`rounded-lg border px-3 py-1.5 text-[11px] ${s.cls}`}>
                <span className="font-semibold">{s.icon} {f.category}</span>
                <span className="ml-2 opacity-80">Validation agent</span>
                <div className="mt-0.5 text-slate-700">{f.comment}</div>
              </div>
            );
          })}
        </div>
      )}

      {/* report form */}
      {open && (
        <form
          className="mb-3 rounded-lg border border-slate-200 bg-slate-50 p-3"
          onSubmit={(e) => { e.preventDefault(); report.mutate({ category, severity, comment }); }}
        >
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value as (typeof CATEGORIES)[number])}
              className="rounded-lg border border-slate-300 px-2 py-1 text-xs"
              aria-label="Issue category"
            >
              {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <select
              value={severity}
              onChange={(e) => setSeverity(e.target.value as Feedback['severity'])}
              className="rounded-lg border border-slate-300 px-2 py-1 text-xs"
              aria-label="Issue severity"
            >
              <option value="error">error</option>
              <option value="warning">warning</option>
              <option value="info">info</option>
            </select>
          </div>
          <textarea
            className="mt-2 w-full rounded-lg border border-slate-300 p-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-200"
            rows={2}
            placeholder="Describe the quality issue (what's wrong and, ideally, what you expected)…"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
          />
          <div className="mt-2 flex items-center gap-2">
            <button
              disabled={report.isPending || comment.trim().length === 0}
              className="rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-40"
            >
              {report.isPending ? 'Submitting…' : 'Submit report'}
            </button>
            <span className="text-[11px] text-slate-400">
              Reported issues are recorded on this generation; a reviewer can use them when requesting changes at the gate.
            </span>
          </div>
        </form>
      )}

      {/* human reports */}
      {human.length > 0 ? (
        <div className="space-y-1.5">
          {human.map((f) => {
            const s = SEV_META[f.severity];
            const resolved = f.status === 'resolved';
            return (
              <div
                key={f.id}
                className={`rounded-lg border px-3 py-1.5 text-[11px] ${resolved ? 'border-slate-200 bg-slate-50 opacity-70' : s.cls}`}
              >
                <div className="flex items-center gap-2">
                  <span className="font-semibold">{s.icon} {f.category}</span>
                  {resolved && <span className="rounded-full bg-emerald-100 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-700">resolved</span>}
                  <span className="ml-auto text-[10px] opacity-70">{new Date(f.createdAt).toLocaleString()}</span>
                  {!resolved && canResolve && (
                    <button
                      type="button"
                      onClick={() => resolve.mutate(f.id)}
                      disabled={resolve.isPending}
                      className="rounded border border-current px-1.5 py-0.5 text-[10px] font-semibold hover:bg-white/50"
                    >
                      Resolve
                    </button>
                  )}
                </div>
                {f.comment && <div className="mt-0.5 text-slate-700">{f.comment}</div>}
              </div>
            );
          })}
        </div>
      ) : (
        validation.length === 0 && !open && (
          <div className="text-[11px] text-slate-400">
            No issues reported. The validation agent checks every generation automatically; use “Report an issue” to flag anything else.
          </div>
        )
      )}
    </section>
  );
}
