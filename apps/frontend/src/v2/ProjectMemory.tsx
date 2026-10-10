import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';
import type { ProjectFlow } from '../api/flow';
import { Icon } from '../components/ui/Icon';
import {
  filterMemories, groupMemories, KIND_HINT, KIND_LABEL, SCOPE_LABEL, sourceLabel,
  type MemoryEntry, type MemoryKind,
} from '../lib/memory';
import { Pill } from './bits';

const KINDS = Object.keys(KIND_LABEL) as MemoryKind[];

/** What the team has decided and learned. The platform proposes; a person accepts; only accepted memories reach a prompt. */
export default function ProjectMemory({ projectId, flow }: { projectId: string; flow: ProjectFlow | undefined }) {
  const qc = useQueryClient();
  const [kind, setKind] = useState<MemoryKind | 'all'>('all');
  const [query, setQuery] = useState('');
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState('');
  const q = useQuery({
    queryKey: ['memory', projectId],
    queryFn: () => api.get<{ entries: MemoryEntry[]; canCurate: boolean }>(`/api/projects/${projectId}/memory`),
    refetchInterval: 15_000,
  });
  const canCurate = q.data?.canCurate ?? false;
  const refresh = () => { setError(''); void qc.invalidateQueries({ queryKey: ['memory', projectId] }); };
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : 'That did not work');
  const patch = useMutation({
    mutationFn: (v: { id: string; body: Record<string, unknown> }) => api.patch(`/api/projects/${projectId}/memory/${v.id}`, v.body),
    onSuccess: refresh, onError: fail,
  });
  const toRule = useMutation({ mutationFn: (id: string) => api.post(`/api/projects/${projectId}/rules/from-memory/${id}`), onSuccess: () => { refresh(); void qc.invalidateQueries({ queryKey: ['rules', projectId] }); }, onError: fail });
  const promote = useMutation({ mutationFn: (id: string) => api.post(`/api/projects/${projectId}/memory/${id}/promote`), onSuccess: refresh, onError: fail });
  const remove = useMutation({ mutationFn: (id: string) => api.del(`/api/projects/${projectId}/memory/${id}`), onSuccess: refresh, onError: fail });
  const create = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.post(`/api/projects/${projectId}/memory`, body),
    onSuccess: () => { setAdding(false); refresh(); }, onError: fail,
  });

  const all = filterMemories(q.data?.entries ?? [], { kind, query });
  const { suggested, active, archived } = groupMemories(all);
  const card = (m: MemoryEntry) => (
    <MemoryCard key={m.id} m={m} flow={flow} canChange={m.scope === 'user' || canCurate} canPromote={canCurate && m.scope === 'project' && m.status === 'active'}
      onStatus={(status) => patch.mutate({ id: m.id, body: { status } })}
      onSave={(body) => patch.mutate({ id: m.id, body })} onPromote={() => promote.mutate(m.id)} onMakeRule={() => toRule.mutate(m.id)} onDelete={() => remove.mutate(m.id)} />
  );
  return (
    <div data-testid="v2-memory-tab">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search memory" aria-label="Search memory"
          className="min-w-0 flex-1 rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm" />
        <button type="button" onClick={() => setAdding((v) => !v)} data-testid="v2-memory-add"
          className="inline-flex items-center gap-1 rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-brand-700">
          <Icon name="plus" size={14} />Add memory
        </button>
      </div>
      <div className="mb-3 flex flex-wrap gap-1.5">
        {(['all', ...KINDS] as const).map((k) => (
          <button key={k} type="button" onClick={() => setKind(k)} aria-pressed={kind === k}
            className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${kind === k ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>
            {k === 'all' ? 'All' : KIND_LABEL[k]}
          </button>
        ))}
      </div>
      {error && <div role="alert" className="mb-3 rounded-lg bg-bared-50 px-3 py-2 text-sm text-bared-700">{error}</div>}
      {adding && <AddMemory flow={flow} canCurate={canCurate} busy={create.isPending} onCancel={() => setAdding(false)} onSave={(b) => create.mutate(b)} />}
      {q.isLoading && <div className="animate-pulse text-sm text-slate-400">Loading…</div>}

      {suggested.length > 0 && (
        <section className="mb-5" data-testid="v2-memory-suggested">
          <h3 className="mb-1 text-sm font-semibold text-navy">Suggested · {suggested.length}</h3>
          <p className="mb-2 text-xs text-slate-500">{canCurate ? 'Accept the ones worth keeping. Nothing here is used until you do.' : 'A project manager or architect will review these. Nothing here is used until then.'}</p>
          <div className="space-y-2">{suggested.map(card)}</div>
        </section>
      )}
      <section className="mb-5" data-testid="v2-memory-active">
        <h3 className="mb-1 text-sm font-semibold text-navy">Remembered · {active.length}</h3>
        {active.length === 0 && !q.isLoading && (
          <div className="rounded-lg bg-slate-100 p-4 text-center text-sm text-slate-500">
            Nothing remembered yet. Memories appear here when you answer a clarifying question or ask for changes, or you can add one.
          </div>
        )}
        <div className="space-y-2">{active.map(card)}</div>
      </section>
      {archived.length > 0 && (
        <details className="mb-2">
          <summary className="cursor-pointer text-sm font-semibold text-slate-500">Archived · {archived.length}</summary>
          <div className="mt-2 space-y-2">{archived.map(card)}</div>
        </details>
      )}
    </div>
  );
}

function MemoryCard({ m, flow, canChange, canPromote, onStatus, onSave, onPromote, onMakeRule, onDelete }: {
  m: MemoryEntry; flow: ProjectFlow | undefined; canChange: boolean; canPromote: boolean;
  onStatus: (s: string) => void; onSave: (b: Record<string, unknown>) => void; onPromote: () => void; onMakeRule: () => void; onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(m.title);
  const [body, setBody] = useState(m.body);
  const stage = m.stage != null ? flow?.stages.find((s) => s.template === m.stage)?.name ?? `Stage type ${m.stage}` : null;
  const btn = 'rounded-md px-2 py-0.5 text-xs font-semibold';
  return (
    <div className={`rounded-xl border bg-white p-3 ${m.status === 'suggested' ? 'border-amber-300' : 'border-slate-200'}`} data-testid="v2-memory-card" data-status={m.status}>
      {editing ? (
        <div className="space-y-2">
          <input value={title} onChange={(e) => setTitle(e.target.value)} aria-label="Title" className="w-full rounded-lg border border-slate-300 px-2 py-1 text-sm font-semibold" />
          <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={3} aria-label="Memory" className="w-full rounded-lg border border-slate-300 px-2 py-1 text-sm" />
          <div className="flex gap-2">
            <button type="button" className={`${btn} bg-brand-600 text-white`} onClick={() => { onSave({ title, body }); setEditing(false); }}>Save</button>
            <button type="button" className={`${btn} text-slate-500`} onClick={() => { setTitle(m.title); setBody(m.body); setEditing(false); }}>Cancel</button>
          </div>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-1.5">
            <Pill tone="brand">{KIND_LABEL[m.kind]}</Pill>
            <Pill tone={m.scope === 'org' ? 'green' : 'slate'}>{SCOPE_LABEL[m.scope]}</Pill>
            {stage && <Pill>{stage}</Pill>}
            {m.uses > 0 && <span className="ml-auto text-[11px] text-slate-400" title="Times a stage was given this memory">Used {m.uses}×</span>}
          </div>
          <div className="mt-1.5 text-sm font-semibold text-navy">{m.title}</div>
          <div className="mt-0.5 whitespace-pre-wrap text-[13px] text-slate-700">{m.body}</div>
          <div className="mt-1 text-[11px] text-slate-400">{sourceLabel(m)}</div>
          {canChange && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {m.status === 'suggested' && <>
                <button type="button" className={`${btn} bg-brand-600 text-white`} onClick={() => onStatus('active')} data-testid="v2-memory-accept">Accept</button>
                <button type="button" className={`${btn} border border-slate-300 text-slate-600`} onClick={() => onStatus('rejected')} data-testid="v2-memory-dismiss">Dismiss</button>
              </>}
              <button type="button" className={`${btn} border border-slate-300 text-slate-600`} onClick={() => setEditing(true)}>Edit</button>
              {m.status === 'active' && <button type="button" className={`${btn} border border-slate-300 text-slate-600`} onClick={() => onStatus('archived')}>Archive</button>}
              {m.status === 'archived' && <button type="button" className={`${btn} border border-slate-300 text-slate-600`} onClick={() => onStatus('active')}>Restore</button>}
              {canPromote && m.kind !== 'working_style' && <button type="button" className={`${btn} border border-brand-300 text-brand-700`} onClick={onMakeRule} title="Turn this into a binding rule in Project Mindset → Rules" data-testid="v2-memory-make-rule">Make it a rule</button>}
              {canPromote && <button type="button" className={`${btn} border border-brand-300 text-brand-700`} onClick={onPromote} title="Give this to every project in the organisation">Share with all projects</button>}
              <button type="button" className={`${btn} ml-auto text-bared-600`} onClick={onDelete} aria-label="Delete memory"><Icon name="trash" size={13} /></button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function AddMemory({ flow, canCurate, busy, onSave, onCancel }: {
  flow: ProjectFlow | undefined; canCurate: boolean; busy: boolean; onSave: (b: Record<string, unknown>) => void; onCancel: () => void;
}) {
  const [kind, setKind] = useState<MemoryKind>('decision');
  const [scope, setScope] = useState<'project' | 'user'>('project');
  const [stage, setStage] = useState('');
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const templates = [...new Map((flow?.stages ?? []).map((s) => [s.template, s.name])).entries()];
  const field = 'w-full rounded-lg border border-slate-300 px-2 py-1.5 text-sm';
  return (
    <form className="mb-4 space-y-2 rounded-xl border border-brand-300 bg-brand-50/40 p-3" data-testid="v2-memory-form"
      onSubmit={(e) => { e.preventDefault(); onSave({ kind, scope: kind === 'working_style' ? 'user' : scope, stage: stage ? Number(stage) : null, title, body }); }}>
      <div className="grid grid-cols-2 gap-2">
        <label className="text-xs text-slate-500">Kind
          <select value={kind} onChange={(e) => setKind(e.target.value as MemoryKind)} className={field}>{KINDS.map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}</select>
        </label>
        <label className="text-xs text-slate-500">Who it is for
          <select value={kind === 'working_style' ? 'user' : scope} disabled={kind === 'working_style'} onChange={(e) => setScope(e.target.value as 'project' | 'user')} className={field}>
            <option value="project">This project</option><option value="user">Just me</option>
          </select>
        </label>
      </div>
      <p className="text-xs text-slate-500">{KIND_HINT[kind]}</p>
      <label className="block text-xs text-slate-500">Applies to
        <select value={stage} onChange={(e) => setStage(e.target.value)} className={field}>
          <option value="">Every stage</option>
          {templates.map(([t, name]) => <option key={t} value={t}>{name}</option>)}
        </select>
      </label>
      <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Short title, e.g. Queues use SQS FIFO" aria-label="Title" className={field} />
      <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={3} placeholder="What should be remembered?" aria-label="Memory" className={field} />
      <div className="flex items-center gap-2">
        <button type="submit" disabled={busy || title.trim().length < 3 || !body.trim()} className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-50">Save</button>
        <button type="button" onClick={onCancel} className="text-sm text-slate-500">Cancel</button>
        {scope === 'project' && kind !== 'working_style' && !canCurate && <span className="text-xs text-slate-500">A project manager or architect will review it first.</span>}
      </div>
    </form>
  );
}
