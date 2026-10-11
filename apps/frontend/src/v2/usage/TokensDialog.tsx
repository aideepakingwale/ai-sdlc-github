import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { api, TOKENS_EVENT, type TokensEvent } from '../../api/client';

/** Opens a request-tokens dialog from anywhere (the balance line's button) with the same shape the API's refusal has. */
export function openTokensDialog(detail: TokensEvent): void {
  window.dispatchEvent(new CustomEvent<TokensEvent>(TOKENS_EVENT, { detail }));
}

/**
 * Explains a refusal: this project's (or the organisation's) tokens are used up, so new work does not start, or one task hit its safety limit.
 * Listens for the app-wide event the API client raises, so it appears wherever the person was when they tried to start something.
 * From here a person can ask for more tokens; an administrator is offered the allocation console.
 */
export default function TokensDialog({ isAdmin, projectId, onOpenAllocations }: { isAdmin: boolean; projectId: string | null; onOpenAllocations: () => void }) {
  const [event, setEvent] = useState<TokensEvent | null>(null);
  const [note, setNote] = useState('');
  const [done, setDone] = useState('');
  const [err, setErr] = useState('');
  const qc = useQueryClient();
  useEffect(() => {
    const on = (e: Event) => { setEvent((e as CustomEvent<TokensEvent>).detail); setNote(''); setDone(''); setErr(''); };
    window.addEventListener(TOKENS_EVENT, on);
    return () => window.removeEventListener(TOKENS_EVENT, on);
  }, []);
  const pid = event?.details.projectId ?? projectId;
  const ask = useMutation({
    mutationFn: () => api.post<{ already: boolean }>(`/api/projects/${pid}/token-requests`, { note }),
    onSuccess: (r) => { setDone(r.already ? 'You have already asked; an administrator has your request.' : 'Asked. An administrator has been told, and your team can see the request.'); void qc.invalidateQueries({ queryKey: ['pools'] }); },
    onError: (e) => setErr(e instanceof Error ? e.message : 'Could not send the request'),
  });
  if (!event) return null;
  const ceiling = event.code === 'TASK_TOKEN_CEILING';
  const canAsk = !ceiling && event.details.canRequest !== false && Boolean(pid);
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4" role="alertdialog" aria-modal="true" aria-labelledby="tokens-dialog-title" data-testid="v2-tokens-dialog">
      <div className="w-full max-w-md rounded-xl bg-white p-5 shadow-xl">
        <h3 id="tokens-dialog-title" className="font-display text-lg font-bold text-navy">{ceiling ? 'That task was stopped' : event.details.pool === 'organisation' ? 'The organisation is out of tokens' : 'This project is out of tokens'}</h3>
        <p className="mt-2 text-sm text-slate-700" data-testid="v2-tokens-message">{event.message}</p>
        {!ceiling && <p className="mt-2 text-xs text-slate-500">You can still read everything, review and approve finished work, and ask for more tokens. New work in the generation workspace starts again as soon as the balance is above zero.</p>}
        {canAsk && !done && (
          <div className="mt-3">
            <label className="block text-sm font-medium text-navy" htmlFor="tokens-note">What you need them for (optional)</label>
            <textarea id="tokens-note" value={note} onChange={(e) => setNote(e.target.value)} maxLength={300} rows={2} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm" data-testid="v2-tokens-note" />
          </div>
        )}
        {done && <p className="mt-3 rounded-lg bg-emerald-50 p-2 text-sm text-emerald-800" role="status" data-testid="v2-tokens-done">{done}</p>}
        {err && <p className="mt-2 text-sm text-red-700" role="alert">{err}</p>}
        <div className="mt-4 flex flex-wrap justify-end gap-2">
          {isAdmin && !ceiling && <button type="button" className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm" onClick={() => { setEvent(null); onOpenAllocations(); }} data-testid="v2-tokens-allocate">Open allocations</button>}
          <button type="button" className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm" onClick={() => setEvent(null)} data-testid="v2-tokens-close">Close</button>
          {canAsk && !done && <button type="button" disabled={ask.isPending} className="rounded-lg bg-brand-600 px-4 py-1.5 text-sm font-medium text-white disabled:opacity-40" onClick={() => ask.mutate()} data-testid="v2-tokens-ask">{ask.isPending ? 'Sending…' : 'Ask for more tokens'}</button>}
        </div>
      </div>
    </div>
  );
}
