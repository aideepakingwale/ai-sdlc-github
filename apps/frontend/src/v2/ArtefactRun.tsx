import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { approxTokens, groupContext, ROLE_LABEL, type ArtefactRunResponse } from '../lib/artefactRun';
import { Pill } from './bits';

/** "How this was made": the specialist agent, its model, the context it was given and its prompt, with a one-artefact Regenerate. */
export default function ArtefactRun({ projectId, artefactId }: { projectId: string; artefactId: string }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [showPrompt, setShowPrompt] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const q = useQuery({
    queryKey: ['artefact-run', projectId, artefactId],
    queryFn: () => api.get<ArtefactRunResponse>(`/api/projects/${projectId}/artefacts/${artefactId}/run`),
  });
  useEffect(() => () => { if (timer.current) clearInterval(timer.current); }, []);
  const d = q.data;
  const run = d?.run;
  const regen = useMutation({
    mutationFn: () => api.post(`/api/projects/${projectId}/phase/${d!.phase}/regenerate`, { fields: [d!.field] }),
    onSuccess: () => {
      setBusy(true); setError('');
      timer.current = setInterval(async () => {
        try {
          const j = await api.get<{ running: boolean }>(`/api/projects/${projectId}/phase/${d!.phase}/job`);
          if (!j.running) {
            if (timer.current) clearInterval(timer.current);
            setBusy(false);
            for (const k of ['artefacts', 'flow', 'project', 'phase', 'parts']) void qc.invalidateQueries({ queryKey: [k, projectId] });
          }
        } catch { /* keep polling */ }
      }, 2000);
    },
    onError: (e) => setError(e instanceof Error ? e.message : 'Could not start'),
  });
  if (q.isLoading || !run) return null;
  const groups = groupContext(run.context);
  const total = run.context.reduce((n, i) => n + (i.chars ?? 0), 0);
  return (
    <div className="shrink-0 border-t border-slate-200 bg-slate-50" data-testid="v2-artefact-run">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} className="flex w-full items-center gap-2 px-4 py-2 text-left text-xs">
        <span className="font-semibold text-navy">How this was made</span>
        <Pill tone="brand">{run.agentName}</Pill>
        <Pill>{ROLE_LABEL[run.role] ?? run.role}</Pill>
        <span className="truncate text-slate-500">{run.model}{run.reused ? ' · kept from an earlier run' : ''}</span>
        <span className="ml-auto text-slate-400">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="max-h-72 space-y-3 overflow-y-auto px-4 pb-3 text-xs">
          <div className="text-slate-600">
            Written by <b>{run.agentName}</b> on <b>{run.model}</b> ({run.provider}) · ≈{approxTokens(total)} tokens of its own context · {run.tokens.prompt.toLocaleString()} in, {run.tokens.completion.toLocaleString()} out · {new Date(run.at).toLocaleString()}
          </div>
          {d?.canRegenerate && d.field && (
            <div className="flex items-center gap-2">
              <button type="button" disabled={busy || regen.isPending} data-testid="v2-run-regenerate"
                onClick={() => { if (window.confirm(`Regenerate only this artefact with ${run.agentName}? Its new version replaces this one in review; the other artefacts of the stage are kept.`)) regen.mutate(); }}
                className="rounded-lg border border-brand-300 px-3 py-1 font-semibold text-brand-700 hover:bg-brand-50 disabled:opacity-50">
                {busy ? 'Regenerating…' : 'Regenerate this artefact'}
              </button>
              {error && <span role="alert" className="text-bared-600">{error}</span>}
            </div>
          )}
          <ul className="space-y-1" aria-label="Context this agent was given">
            {groups.map((g) => (
              <li key={g.layer}>
                <div className="flex justify-between font-semibold text-slate-700"><span>{g.label}</span><span className="font-normal text-slate-400">{g.chars ? `≈${approxTokens(g.chars)} tokens` : ''}</span></div>
                {g.items.map((i, n) => <div key={n} className="truncate pl-3 text-slate-500" title={i.label}>{i.label}{i.totalChars && i.chars && i.totalChars > i.chars ? ` (first ${i.chars.toLocaleString()} of ${i.totalChars.toLocaleString()} characters)` : ''}</div>)}
              </li>
            ))}
          </ul>
          {d?.detail && run.user != null ? (
            <div>
              <button type="button" onClick={() => setShowPrompt((v) => !v)} className="font-semibold text-brand-700">{showPrompt ? 'Hide the prompt' : 'Show the prompt'}</button>
              {showPrompt && <pre className="mt-1 max-h-56 overflow-auto whitespace-pre-wrap rounded bg-white p-2 font-mono text-[11px] text-slate-700" data-testid="v2-run-prompt">{`SYSTEM\n${run.system ?? ''}\n\nUSER\n${run.user}`}</pre>}
            </div>
          ) : <div className="text-slate-400">The prompt text is shown to the stage&rsquo;s reviewers and the project manager.</div>}
        </div>
      )}
    </div>
  );
}
