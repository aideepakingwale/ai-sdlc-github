import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { Icon } from '../components/ui/Icon';
import {
  isDirty, SECTIONS, statusOf, type Connection, type Connections, type ConnKind, type SectionDef, type TestResult,
} from '../lib/connections';
import { Pill } from './bits';

type Draft = Record<string, string | boolean | number>;

/** A project's own Git repository, Jira project, Confluence space and knowledge-base settings, each with a connectivity test. */
export default function ConnectionsPage({ projectId, projectName }: { projectId: string; projectName?: string }) {
  const q = useQuery({
    queryKey: ['connections', projectId],
    queryFn: () => api.get<{ connections: Connections; canEdit: boolean }>(`/api/projects/${projectId}/connections`),
  });
  const canEdit = q.data?.canEdit ?? false;
  return (
    <div className="h-full overflow-y-auto bg-slate-50 p-6" data-testid="v2-connections">
      <div className="mx-auto max-w-3xl">
        <h1 className="mb-1 font-display text-xl font-bold text-navy">Connections{projectName ? ` · ${projectName}` : ''}</h1>
        <p className="mb-4 text-sm text-slate-500">
          Where this project keeps its code, tickets, pages and knowledge. Each one has its own address and access token, stored encrypted and never shown again.
          A section you leave without a token of its own uses the platform&rsquo;s shared connection.
          {!canEdit && q.data && ' Only the managing project manager or an administrator can change these.'}
        </p>
        {q.isLoading && <div className="animate-pulse text-sm text-slate-400">Loading…</div>}
        {q.isError && <div role="alert" className="rounded-lg bg-bared-50 px-3 py-2 text-sm text-bared-700">Could not load the connections.</div>}
        <div className="space-y-4">
          {q.data && SECTIONS.map((s) => <Section key={s.kind} def={s} projectId={projectId} saved={q.data.connections[s.kind]} canEdit={canEdit} />)}
        </div>
      </div>
    </div>
  );
}

function Section({ def, projectId, saved, canEdit }: { def: SectionDef; projectId: string; saved: Connection; canEdit: boolean }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<Draft>(saved.settings);
  const [secret, setSecret] = useState('');
  const [clear, setClear] = useState(false);
  const [result, setResult] = useState<TestResult | null>(saved.lastTest);
  const [error, setError] = useState('');
  const [savedAt, setSavedAt] = useState(0);
  useEffect(() => { setDraft(saved.settings); setResult(saved.lastTest); }, [saved]);
  const dirty = isDirty(saved, draft, secret) || clear;
  const status = result && !dirty ? (result.ok ? { tone: 'green' as const, label: 'Connected' } : { tone: 'red' as const, label: 'Test failed' }) : statusOf(def.kind, { ...saved, lastTest: dirty ? null : saved.lastTest });
  const body = () => ({ settings: draft, secrets: def.secret && secret ? { [def.secret.key]: secret } : {}, clearSecret: clear });
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : 'That did not work');
  const save = useMutation({
    mutationFn: () => api.put(`/api/projects/${projectId}/connections/${def.kind}`, body()),
    onSuccess: () => { setSecret(''); setClear(false); setError(''); setSavedAt(Date.now()); void qc.invalidateQueries({ queryKey: ['connections', projectId] }); void qc.invalidateQueries({ queryKey: ['project', projectId] }); },
    onError: fail,
  });
  const test = useMutation({
    mutationFn: () => api.post<{ result: TestResult }>(`/api/projects/${projectId}/connections/${def.kind}/test`, body()),
    onSuccess: (r) => { setError(''); setResult(r.result); void qc.invalidateQueries({ queryKey: ['connections', projectId] }); },
    onError: fail,
  });
  const input = 'w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm disabled:bg-slate-100 disabled:text-slate-500';
  const set = (k: string, v: string | boolean | number) => { setDraft((d) => ({ ...d, [k]: v })); setSavedAt(0); };
  const sameAsJira = def.kind === 'confluence' && draft.sameAsJira === true;
  return (
    <section className="rounded-2xl border border-slate-200 bg-white" data-testid={`v2-conn-${def.kind}`}>
      <header className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-4 py-3">
        <Icon name={def.kind === 'github' ? 'gitbranch' : def.kind === 'kb' ? 'database' : def.kind === 'jira' ? 'tasks' : 'book'} size={18} className="text-brand-600" />
        <h2 className="text-base font-semibold text-navy">{def.title}</h2>
        <span data-testid={`v2-conn-status-${def.kind}`}><Pill tone={status.tone}>{status.label}</Pill></span>
        {saved.updatedBy && <span className="ml-auto text-[11px] text-slate-400">Saved by {saved.updatedBy}</span>}
      </header>
      <div className="space-y-3 px-4 py-3">
        <p className="text-[13px] text-slate-500">{def.blurb}</p>
        {def.kind === 'confluence' && (
          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input type="checkbox" disabled={!canEdit} checked={sameAsJira} onChange={(e) => set('sameAsJira', e.target.checked)} data-testid="v2-conn-same-as-jira" />
            Use the same account and token as Jira
          </label>
        )}
        {def.kind === 'kb' ? <KbFields draft={draft} set={set} canEdit={canEdit} /> : (
          <div className="grid gap-3 sm:grid-cols-2">
            {def.fields.filter((f) => !(sameAsJira && f.key === 'email')).map((f) => (
              <label key={f.key} className="block text-xs text-slate-500">{f.label}
                <input className={input} type={f.type ?? 'text'} disabled={!canEdit} value={String(draft[f.key] ?? '')} placeholder={f.placeholder}
                  onChange={(e) => set(f.key, e.target.value)} data-testid={`v2-conn-${def.kind}-${f.key}`} />
                {f.hint && <span className="mt-0.5 block text-[11px] text-slate-400">{f.hint}</span>}
              </label>
            ))}
            {def.secret && !sameAsJira && (
              <label className="block text-xs text-slate-500 sm:col-span-2">{def.secret.label}
                <input className={input} type="password" autoComplete="new-password" disabled={!canEdit || clear} value={secret}
                  placeholder={saved.secretSet ? '•••••••• saved. Leave blank to keep it' : 'Paste a token'}
                  onChange={(e) => { setSecret(e.target.value); setSavedAt(0); }} data-testid={`v2-conn-${def.kind}-secret`} />
                <span className="mt-0.5 block text-[11px] text-slate-400">{def.secret.hint}</span>
                {saved.secretSet && canEdit && (
                  <button type="button" onClick={() => { setClear((v) => !v); setSecret(''); }} className="mt-1 text-[11px] font-semibold text-bared-600">
                    {clear ? 'Keep the saved token' : 'Remove the saved token'}
                  </button>
                )}
              </label>
            )}
          </div>
        )}
        {error && <div role="alert" className="rounded-lg bg-bared-50 px-3 py-2 text-sm text-bared-700">{error}</div>}
        {result && (
          <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 text-sm" data-testid={`v2-conn-result-${def.kind}`}>
            {result.checks.map((c) => (
              <li key={c.name} className="flex items-start gap-2 px-3 py-1.5">
                <Icon name={c.ok ? 'success' : c.optional ? 'info' : 'error'} size={15} className={c.ok ? 'mt-0.5 text-emerald-600' : c.optional ? 'mt-0.5 text-amber-600' : 'mt-0.5 text-bared-600'} />
                <span className="min-w-0"><span className="font-medium text-navy">{c.name}</span>{c.detail && <span className="block text-xs text-slate-500">{c.detail}</span>}</span>
              </li>
            ))}
            <li className="px-3 py-1 text-[11px] text-slate-400">Tested {new Date(result.at).toLocaleString()} by {result.by}{dirty ? ' · before your latest changes' : ''}</li>
          </ul>
        )}
        {canEdit && (
          <div className="flex items-center gap-2">
            <button type="button" onClick={() => test.mutate()} disabled={test.isPending} data-testid={`v2-conn-test-${def.kind}`}
              className="inline-flex items-center gap-1.5 rounded-lg border border-brand-300 px-3 py-1.5 text-sm font-semibold text-brand-700 hover:bg-brand-50 disabled:opacity-50">
              <Icon name={test.isPending ? 'loader' : 'zap'} size={14} spin={test.isPending} />{test.isPending ? 'Testing…' : 'Test connection'}
            </button>
            <button type="button" onClick={() => save.mutate()} disabled={!dirty || save.isPending} data-testid={`v2-conn-save-${def.kind}`}
              className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-50">Save</button>
            {savedAt > 0 && !dirty && <span className="text-xs text-emerald-600">Saved</span>}
            {def.kind !== 'kb' && <span className="ml-auto text-[11px] text-slate-400">Test uses what is typed above, saved or not.</span>}
          </div>
        )}
      </div>
    </section>
  );
}

function KbFields({ draft, set, canEdit }: { draft: Draft; set: (k: string, v: string | boolean | number) => void; canEdit: boolean }) {
  const rows: Array<[string, string, string]> = [
    ['standards', 'Organisation standards', 'Engineering and security standards shared by every project.'],
    ['artefacts', 'Approved artefacts', 'What earlier approved stages of this project produced.'],
    ['codebase', 'Codebase files', 'Source files uploaded or generated for this project.'],
  ];
  return (
    <div className="space-y-2">
      {rows.map(([k, label, hint]) => (
        <label key={k} className="flex items-start gap-2 text-sm text-slate-700">
          <input type="checkbox" className="mt-1" disabled={!canEdit} checked={draft[k] !== false} onChange={(e) => set(k, e.target.checked)} data-testid={`v2-conn-kb-${k}`} />
          <span><span className="font-medium text-navy">{label}</span><span className="block text-xs text-slate-500">{hint}</span></span>
        </label>
      ))}
      <label className="block text-xs text-slate-500">Snippets per question
        <input className="w-24 rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm" type="number" min={1} max={12} disabled={!canEdit}
          value={Number(draft.topK ?? 4)} onChange={(e) => set('topK', Number(e.target.value))} data-testid="v2-conn-kb-topK" />
      </label>
    </div>
  );
}

export type { ConnKind };
