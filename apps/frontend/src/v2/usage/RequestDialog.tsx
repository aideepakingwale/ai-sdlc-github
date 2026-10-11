import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../api/client';
import { activityLabel, captureReason, statusLabel, tokensFull, type RequestDetail } from '../../lib/usage';

/**
 * One request behind a row of the usage table: every attempt (who was tried, what failed and why) and, for a request that went wrong,
 * the prompt that was sent and the response that came back. The project's manager and administrators only: the text can hold the
 * project's documents, and it is deleted after a few days (the server says how many).
 */
export default function RequestDialog({ projectId, requestId, onClose }: { projectId: string; requestId: string; onClose: () => void }) {
  const q = useQuery({
    queryKey: ['usage-request', projectId, requestId],
    queryFn: () => api.get<RequestDetail>(`/api/projects/${projectId}/usage/requests/${encodeURIComponent(requestId)}`),
    retry: false,
  });
  const d = q.data;
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true" aria-labelledby="usage-request-title" data-testid="v2-usage-request">
      <div className="flex max-h-[90vh] w-full max-w-4xl flex-col rounded-xl bg-white shadow-xl">
        <div className="flex items-start justify-between gap-3 border-b border-slate-200 p-4">
          <div>
            <h3 id="usage-request-title" className="font-display text-lg font-bold text-navy">Request detail</h3>
            {d && <p className="text-xs text-slate-500">{activityLabel(d.activity)}{d.stage ? ` · stage ${d.stage}` : ''} · {d.person ?? 'system'} · {new Date(d.ts).toLocaleString()} · {tokensFull(d.totalTokens)} tokens</p>}
          </div>
          <button type="button" className="rounded-lg border border-slate-300 px-3 py-1 text-sm" onClick={onClose} data-testid="v2-usage-request-close">Close</button>
        </div>
        <div className="overflow-y-auto p-4 text-sm">
          {q.isLoading && <div className="animate-pulse text-slate-400">Loading…</div>}
          {q.isError && <p className="text-red-700" role="alert">{q.error instanceof Error ? q.error.message : 'Could not load this request'}</p>}
          {d && (
            <div className="space-y-4">
              <section>
                <h4 className="mb-1 font-medium text-navy">Attempts</h4>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs" data-testid="v2-usage-request-attempts">
                    <thead><tr className="text-left uppercase tracking-wide text-slate-500"><th className="py-1">#</th><th>Provider and model</th><th>Outcome</th><th>What the provider said</th><th className="text-right">Tokens</th><th className="text-right">Time</th></tr></thead>
                    <tbody>{d.attempts.map((a) => (
                      <tr key={a.attempt} className="border-t border-slate-200 align-top">
                        <td className="py-1">{a.attempt}</td><td>{a.provider} {a.model}</td><td>{statusLabel(a.status)}</td>
                        <td className="max-w-xs break-words">{a.error ?? (a.errorClass ? a.errorClass : '')}{a.stopReason && a.status !== 'ok' ? ` (${a.stopReason})` : ''}</td>
                        <td className="text-right tabular-nums">{tokensFull(a.totalTokens)}</td><td className="text-right tabular-nums">{(a.latencyMs / 1000).toFixed(1)}s</td></tr>))}</tbody>
                  </table>
                </div>
              </section>
              {d.capture ? <Captured c={d.capture} /> : <p className="rounded-lg bg-slate-50 p-3 text-slate-600" data-testid="v2-usage-request-nocapture">{d.captureNote}</p>}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Captured({ c }: { c: NonNullable<RequestDetail['capture']> }) {
  return (
    <>
      <p className="rounded-lg bg-amber-50 p-3 text-amber-900" data-testid="v2-usage-request-reason">
        <span className="font-medium">{captureReason(c.reason)}.</span> {c.error}
        <span className="mt-1 block text-xs">Kept {c.expiresAt ? `until ${new Date(c.expiresAt).toLocaleDateString()}` : 'for a limited time'}. It can contain the project documents, so please do not share it outside the project.</span>
      </p>
      <section data-testid="v2-usage-request-prompt">
        <div className="mb-1 flex items-center justify-between"><h4 className="font-medium text-navy">What was sent</h4>{c.messages && <Copy text={c.messages.map((m) => `[${m.role}]\n${m.content}`).join('\n\n')} />}</div>
        {c.messages ? c.messages.map((m, i) => (
          <details key={i} open={i === c.messages!.length - 1} className="mb-2 rounded-lg border border-slate-200">
            <summary className="cursor-pointer select-none bg-slate-50 px-3 py-1 text-xs font-medium uppercase tracking-wide text-slate-600">{m.role} · {m.content.length.toLocaleString('en-GB')} characters</summary>
            <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words p-3 text-xs">{m.content}</pre>
          </details>
        )) : <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-slate-200 p-3 text-xs">{c.promptText ?? 'Nothing was kept.'}</pre>}
        {c.promptCut && <p className="text-xs text-slate-500">The prompt was longer than the server keeps; the end is not shown.</p>}
      </section>
      <section data-testid="v2-usage-request-response">
        <div className="mb-1 flex items-center justify-between"><h4 className="font-medium text-navy">What came back</h4>{c.response && <Copy text={c.response} />}</div>
        <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-slate-200 p-3 text-xs">{c.response ?? 'The provider returned no text.'}</pre>
        {c.responseCut && <p className="text-xs text-slate-500">The response was longer than the server keeps; the end is not shown.</p>}
      </section>
    </>
  );
}

function Copy({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button type="button" className="text-xs text-brand-600 hover:underline" onClick={() => { void navigator.clipboard?.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1500); }).catch(() => undefined); }}>
      {done ? 'Copied' : 'Copy'}
    </button>
  );
}
