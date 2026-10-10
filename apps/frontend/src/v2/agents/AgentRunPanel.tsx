import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../api/client';
import { outputFileName, outputText, parseRunValue, tokensLabel, type RunForm, type RunHistoryItem, type RunOutcome } from '../../lib/agents';
import { Pill } from '../bits';

const btn = 'rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:border-brand-400 disabled:opacity-50';
const primary = 'rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-50';
const field = 'w-full rounded-lg border border-slate-300 bg-white px-2 py-1 text-sm focus:border-brand-500 focus:outline-none';
const errText = (e: unknown, fallback: string) => (e instanceof Error ? e.message : fallback);

function save(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
  const a = document.createElement('a');
  a.href = url; a.download = name; a.click();
  URL.revokeObjectURL(url);
}

/**
 * Run an approved agent or skill when you ask. On its own (no stage) the answer is shown here and kept in the history; with a stage, what it
 * writes is saved into that stage as artefacts. The project fills in what it can (an approved artefact, the rules, the stack); anything you type wins.
 */
export default function AgentRunPanel({ projectId, defId, stageKey, onBack, onRan }: { projectId: string; defId: string; stageKey?: string | null; onBack?: () => void; onRan?: () => void }) {
  const qc = useQueryClient();
  const formKey = ['agent-run-form', projectId, defId, stageKey ?? ''];
  const q = useQuery({ queryKey: formKey, queryFn: () => api.get<RunForm>(`/api/projects/${projectId}/agents/${defId}/run-form${stageKey ? `?stage=${encodeURIComponent(stageKey)}` : ''}`), retry: false });
  const hist = useQuery({ queryKey: ['agent-run-history', projectId, defId], queryFn: () => api.get<{ runs: RunHistoryItem[] }>(`/api/projects/${projectId}/agent-runs?defId=${defId}&limit=10`) });
  const [vals, setVals] = useState<Record<string, string>>({});
  const [replace, setReplace] = useState<Record<string, boolean>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [out, setOut] = useState<RunOutcome | null>(null);
  const [message, setMessage] = useState('');
  const form = q.data;

  const run = useMutation({
    mutationFn: () => {
      const inputs: Record<string, unknown> = {}; const errs: Record<string, string> = {};
      for (const i of form?.inputs ?? []) {
        const text = vals[i.name] ?? '';
        if (!text.trim() || (i.prefill && !replace[i.name])) continue;
        const r = parseRunValue(text, i.type);
        if (r.ok) inputs[i.name] = r.value; else errs[i.name] = r.error;
      }
      setErrors(errs);
      if (Object.keys(errs).length) throw new Error('Check the highlighted values');
      return api.post<RunOutcome>(`/api/projects/${projectId}/agents/${defId}/run`, { inputs, stageKey: stageKey ?? null });
    },
    onSuccess: (r) => {
      setOut(r); setMessage('');
      void qc.invalidateQueries({ queryKey: ['agent-run-history', projectId] });
      void qc.invalidateQueries({ queryKey: ['agent-project-usage', projectId] });
      if (r.saved.length) for (const k of ['artefacts', 'project', 'flow']) void qc.invalidateQueries({ queryKey: [k, projectId] });
      onRan?.();
    },
    onError: (e) => { setOut(null); setMessage(errText(e, 'It did not run')); },
  });

  if (q.isError) return <div className="rounded-xl border border-slate-300 bg-white p-4 text-sm text-slate-700" role="alert">{errText(q.error, 'Could not open it')}{onBack && <div className="mt-2"><button type="button" className={btn} onClick={onBack}>← Back</button></div>}</div>;
  if (!form) return <div className="animate-pulse text-sm text-slate-400">Loading…</div>;
  const inStage = Boolean(stageKey);
  return (
    <div data-testid="v2-run-panel" data-stage={stageKey ?? ''}>
      {onBack && <button type="button" onClick={onBack} className="text-xs font-semibold text-brand-700 hover:underline" data-testid="v2-run-back">← Back</button>}
      <div className="mt-1 flex flex-wrap items-center gap-2">
        <h3 className="font-display text-xl font-bold text-navy">Run {form.def.name}</h3><Pill tone="green">Approved v{form.def.version}</Pill>
        <Pill tone={inStage ? 'amber' : 'slate'}>{inStage ? 'Saves into this stage' : 'On its own'}</Pill>
      </div>
      <p className="text-sm text-slate-600">{form.def.description || 'No description.'}</p>
      <p className="mt-1 text-xs text-slate-500">{inStage ? 'What it writes becomes artefacts of this stage, with a record of how it was made.' : 'The answer is shown here and kept in the history. Nothing in your stages changes.'}</p>
      {!form.canRun && <div className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900" role="note" data-testid="v2-run-why">{form.why}</div>}

      <div className="mt-3 space-y-3 rounded-xl border border-slate-300 bg-white p-4">
        {form.inputs.map((i) => {
          const filled = Boolean(i.prefill) && !replace[i.name];
          return (
            <div key={i.name} data-testid="v2-run-input">
              <div className="flex flex-wrap items-center gap-2 text-xs"><b className="font-mono text-slate-800">{i.name}</b><span className="text-slate-400">{i.type}{i.required ? '' : ' · optional'}</span>
                {i.description && <span className="text-slate-500">{i.description}</span>}</div>
              {filled ? (
                <div className="mt-1 rounded-lg border border-emerald-200 bg-emerald-50 p-2 text-xs text-emerald-900" data-testid="v2-run-prefill">
                  <div className="flex items-center justify-between gap-2"><span>Filled from <b>{i.prefillFrom}</b></span><button type="button" className="font-semibold underline" onClick={() => setReplace((r) => ({ ...r, [i.name]: true }))}>Use my own</button></div>
                  <div className="mt-1 line-clamp-3 whitespace-pre-wrap text-emerald-800">{(i.prefill ?? '').slice(0, 400)}</div>
                </div>
              ) : i.type === 'boolean' ? (
                <select aria-label={i.name} className={`${field} mt-1`} value={vals[i.name] ?? ''} onChange={(e) => setVals((v) => ({ ...v, [i.name]: e.target.value }))}><option value="">Not set</option><option>yes</option><option>no</option></select>
              ) : (
                <textarea aria-label={i.name} data-testid={`v2-run-field-${i.name}`} rows={i.type === 'string' || i.type === 'list' || i.type === 'object' ? 3 : 1} spellCheck={false} className={`${field} mt-1 ${i.type === 'object' ? 'font-mono text-xs' : ''}`}
                  value={vals[i.name] ?? ''} placeholder={i.type === 'list' ? 'One item per line' : i.type === 'object' ? '{ "key": "value" }, or plain text' : i.required ? 'Required' : 'Optional'} onChange={(e) => setVals((v) => ({ ...v, [i.name]: e.target.value }))} />
              )}
              {errors[i.name] && <div className="mt-0.5 text-xs text-bared-700" role="alert">{errors[i.name]}</div>}
            </div>
          );
        })}
        {!form.inputs.length && <div className="text-sm text-slate-500">It takes no inputs.</div>}
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className={primary} disabled={run.isPending || !form.canRun} data-testid="v2-run-go" onClick={() => run.mutate()}>{run.isPending ? 'Running…' : inStage ? '▶ Run and save to the stage' : `▶ Run ${form.def.kind}`}</button>
          <span className="text-xs text-slate-500">Writes: {form.outputs.map((o) => `${o.name} → ${o.artefactType}`).join(', ') || 'nothing'}</span>
        </div>
        {message && <div className="rounded-lg bg-bared-200 px-3 py-1.5 text-xs text-bared-700" role="alert" data-testid="v2-run-error">{message}</div>}
      </div>

      {out && (
        <div className="mt-3 space-y-2 rounded-xl border border-slate-300 bg-white p-4" data-testid="v2-run-result">
          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500"><Pill tone="green">Done</Pill>{out.mock && <Pill tone="amber">Offline model</Pill>}<span>{tokensLabel(out.tokens)} tokens · {out.provider}/{out.model}</span></div>
          {out.saved.length > 0 && <div className="rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-900" data-testid="v2-run-saved">Saved to the stage: {out.saved.map((s) => `${s.title} (${s.type})`).join(', ')}.</div>}
          {out.warnings.map((w) => <div key={w} className="rounded bg-amber-50 px-2 py-1 text-xs text-amber-800">{w}</div>)}
          {out.outputsDetail.map((o) => {
            const text = outputText(out.outputs[o.name], o.format);
            return (
              <div key={o.name} className="rounded-lg border border-slate-200" data-testid="v2-run-output">
                <div className="flex items-center justify-between gap-2 border-b border-slate-200 px-3 py-1.5 text-xs"><span><b>{o.name}</b> <span className="text-slate-400">{o.artefactType} · {o.format}</span></span>
                  <span className="flex gap-1"><button type="button" className={btn} onClick={() => void navigator.clipboard?.writeText(text)}>Copy</button><button type="button" className={btn} onClick={() => save(outputFileName(out.def.name, o.name, o.format), text)}>Download</button></span></div>
                <pre className="max-h-72 overflow-auto whitespace-pre-wrap p-3 text-sm">{text}</pre>
              </div>
            );
          })}
          <details><summary className="cursor-pointer text-xs font-semibold text-slate-600">What it was given</summary><ul className="mt-1 space-y-0.5 text-xs text-slate-600">{out.context.map((c, i) => <li key={i}>{c.label} · {c.chars.toLocaleString()} characters</li>)}</ul></details>
        </div>
      )}

      {!inStage && (
        <div className="mt-4" data-testid="v2-run-history">
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Recent runs</div>
          {!(hist.data?.runs.length) ? <div className="text-xs text-slate-400">Nothing yet.</div> : (
            <ul className="space-y-1.5">{hist.data.runs.map((r) => (
              <li key={r.id} className="rounded-lg border border-slate-200 bg-white p-2 text-xs" data-testid="v2-run-history-item">
                <div className="flex flex-wrap items-center justify-between gap-2"><span><b>v{r.version}</b> · {r.by || 'someone'} · {new Date(r.at).toLocaleString()}{r.stage ? ` · saved to ${r.stage}` : ''}</span><span className="text-slate-400">{tokensLabel(r.tokens)} tokens</span></div>
                <details className="mt-1"><summary className="cursor-pointer text-slate-500">Answer</summary><pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-1.5">{JSON.stringify(r.outputs, null, 2)}</pre></details>
              </li>))}</ul>)}
        </div>
      )}
    </div>
  );
}
