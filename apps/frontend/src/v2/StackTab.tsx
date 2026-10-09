import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';
import { decidedCount, draftError, fromDraft, groupLayers, identifiedIds, render, sourceLabel, STATUS_TONE, STATUS_WORD, toDraft,
  type EntryDraft, type LayerDef, type ProjectConfig, type StackEntry, type StackPreset } from '../lib/stackConfig';
import { Pill } from './bits';

interface ConfigResponse { config: ProjectConfig; canEdit: boolean; catalog: { layers: LayerDef[] }; presets: StackPreset[]; summary: string }

/**
 * Project Context -> Stack. The stack by layer, as projectconfig.json holds it: what a person pinned, what the platform identified in
 * the project's documents (stages 1 to 3) or its codebase, and what is left open for the Technical Architect.
 */
export default function StackTab({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<EntryDraft | null>(null);
  const [error, setError] = useState('');
  const [note, setNote] = useState('');
  const q = useQuery({ queryKey: ['project-config', projectId], queryFn: () => api.get<ConfigResponse>(`/api/projects/${projectId}/config`) });
  const refresh = () => { void qc.invalidateQueries({ queryKey: ['project-config', projectId] }); void qc.invalidateQueries({ queryKey: ['project', projectId] }); void qc.invalidateQueries({ queryKey: ['projects'] }); };
  const save = useMutation({
    mutationFn: (body: { upsert?: unknown[]; remove?: string[]; confirm?: string[] }) => api.put(`/api/projects/${projectId}/config/stack`, body),
    onSuccess: () => { setEditing(null); setError(''); refresh(); },
    onError: (e) => setError(e instanceof Error ? e.message : 'Could not save'),
  });
  const recheck = useMutation({
    mutationFn: () => api.post<{ changes: Array<{ layer: string; to: string }> }>(`/api/projects/${projectId}/config/stack/advise`),
    onSuccess: (r) => { setNote(r.changes.length ? `Found ${r.changes.length} change${r.changes.length === 1 ? '' : 's'} in the documents.` : 'Nothing new in the documents.'); refresh(); },
    onError: (e) => setNote(e instanceof Error ? e.message : 'Could not check the documents'),
  });
  const preset = useMutation({
    mutationFn: (id: string) => api.post(`/api/projects/${projectId}/config/stack/preset/${id}`),
    onSuccess: () => { setNote('Preset applied.'); refresh(); },
    onError: (e) => setNote(e instanceof Error ? e.message : 'Could not apply the preset'),
  });
  if (!q.data) return <div className="animate-pulse text-sm text-slate-400">Loading…</div>;
  const { config, canEdit, catalog, presets } = q.data;
  const entries = config.stack.layers;
  const rows = groupLayers(catalog.layers, entries);
  const toConfirm = identifiedIds(entries);
  const optionsFor = (layer: string) => catalog.layers.find((l) => l.id === layer)?.options ?? [];

  function submit() {
    if (!editing) return;
    const err = draftError(editing);
    if (err) { setError(err); return; }
    save.mutate({ upsert: [fromDraft(editing)] });
  }

  return (
    <div data-testid="v2-stack-tab">
      <div className="mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-slate-300 bg-slate-100 p-3">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-navy">Technology stack</div>
          <div className="truncate text-xs text-slate-500" data-testid="v2-stack-summary">{config.stack.summary || 'Nothing decided yet. The platform fills this in as your stages produce documents, or you can set it here.'}</div>
        </div>
        <span className="ml-auto flex flex-wrap items-center gap-2">
          <Pill title="projectconfig.json, shown in the Files tab">{decidedCount(entries)} of {catalog.layers.length} layers · v{config.version}</Pill>
          {canEdit && <button type="button" onClick={() => recheck.mutate()} disabled={recheck.isPending} data-testid="v2-stack-recheck"
            className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:border-brand-400 disabled:opacity-50">{recheck.isPending ? 'Reading the documents…' : 'Re-check from documents'}</button>}
          <a href={`/api/projects/${projectId}/config/file`} target="_blank" rel="noreferrer" className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:border-brand-400">projectconfig.json</a>
        </span>
      </div>

      {config.stack.conflicts.length > 0 && (
        <div className="mb-3 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900" role="alert" data-testid="v2-stack-conflicts">
          <div className="font-semibold">A document disagrees with something you pinned</div>
          <ul className="mt-1 list-disc pl-4">{config.stack.conflicts.map((c, i) => (
            <li key={i}>{c.layer}: you pinned <b>{c.pinned}</b>, stage {c.stage} says <b>{c.found}</b>{c.evidence ? <> (“{c.evidence}”)</> : null}. Your choice stays; change the pin or the document.</li>
          ))}</ul>
        </div>
      )}
      {toConfirm.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-brand-200 bg-brand-50 px-3 py-2 text-xs text-brand-900" data-testid="v2-stack-identified">
          <span>{toConfirm.length} value{toConfirm.length === 1 ? ' was' : 's were'} identified in your documents. Stages 3 to 6 follow them unless the design gives a reason.</span>
          {canEdit && <button type="button" onClick={() => save.mutate({ confirm: toConfirm })} data-testid="v2-stack-confirm-all" className="ml-auto rounded-lg bg-brand-600 px-3 py-1 font-semibold text-white hover:bg-brand-700">Confirm all</button>}
        </div>
      )}
      {presets.length > 0 && canEdit && (
        <div className="mb-3 flex items-center gap-2 text-xs text-slate-600">
          <label htmlFor="stack-preset">Start from an organisation preset</label>
          <select id="stack-preset" defaultValue="" onChange={(e) => { if (e.target.value && window.confirm('Apply this preset? It pins its layers (your own pins on other layers stay).')) preset.mutate(e.target.value); e.target.value = ''; }}
            className="rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs">
            <option value="">Choose…</option>
            {presets.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
      )}
      {(note || error) && <div className={`mb-2 text-xs ${error ? 'text-red-700' : 'text-slate-600'}`} role="status">{error || note}</div>}

      <ul className="divide-y divide-slate-200 rounded-xl border border-slate-300 bg-white" data-testid="v2-stack-layers">
        {rows.map(({ layer, entries: es }) => (
          <li key={layer.id} className="px-3 py-2.5" data-layer={layer.id}>
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="w-44 shrink-0 text-sm font-semibold text-slate-800">{layer.label}</span>
              <span className="min-w-0 flex-1 text-xs text-slate-500">{layer.hint}</span>
              {canEdit && layer.id !== 'other' && <button type="button" onClick={() => setEditing(toDraft(layer.id))} data-testid={`v2-stack-add-${layer.id}`} className="text-xs font-semibold text-brand-700 hover:underline">{es.length ? '+ Add another' : 'Set'}</button>}
            </div>
            {es.length === 0 && <div className="mt-1 pl-0 text-xs text-slate-400">Not decided yet</div>}
            {es.map((e) => <EntryRow key={e.id} e={e} canEdit={canEdit} onEdit={() => setEditing(toDraft(e.layer, e))} onConfirm={() => save.mutate({ confirm: [e.id] })} onRemove={() => save.mutate({ remove: [e.id] })} />)}
            {editing && editing.layer === layer.id && (
              <EntryForm d={editing} onChange={setEditing} options={optionsFor(layer.id)} onSave={submit} onCancel={() => { setEditing(null); setError(''); }} busy={save.isPending} />
            )}
          </li>
        ))}
      </ul>
      <p className="mt-2 text-[11px] text-slate-400">
        Pinned values are decisions: no stage changes them. Identified values come from your documents or codebase. Open layers are decided by the Technical Architect stage.
        {Object.keys(config.stack.advisor).length > 0 && ` Documents read so far: stage ${Object.keys(config.stack.advisor).filter((k) => k !== '0').join(', ') || '—'}.`}
      </p>
    </div>
  );
}

function EntryRow({ e, canEdit, onEdit, onConfirm, onRemove }: { e: StackEntry; canEdit: boolean; onEdit: () => void; onConfirm: () => void; onRemove: () => void }) {
  return (
    <div className="mt-1 flex flex-wrap items-center gap-2 pl-0" data-testid={`v2-stack-entry-${e.id}`}>
      {e.component && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600">{e.component}</span>}
      <span className={`text-sm ${e.status === 'open' ? 'italic text-slate-400' : 'font-medium text-slate-900'}`}>{e.status === 'open' ? 'To be decided' : render(e)}</span>
      <Pill tone={STATUS_TONE[e.status]} title={[sourceLabel(e), e.evidence && `“${e.evidence}”`, e.rationale].filter(Boolean).join(' · ')}>{STATUS_WORD[e.status]}</Pill>
      <span className="text-[11px] text-slate-400">{sourceLabel(e)}</span>
      {e.notes && <span className="text-[11px] text-slate-500">· {e.notes}</span>}
      {canEdit && (
        <span className="ml-auto flex items-center gap-2 text-xs">
          {e.status === 'identified' && <button type="button" onClick={onConfirm} className="font-semibold text-emerald-700 hover:underline">Confirm</button>}
          <button type="button" onClick={onEdit} className="font-semibold text-brand-700 hover:underline">Edit</button>
          <button type="button" onClick={onRemove} className="text-slate-400 hover:text-red-600">Remove</button>
        </span>
      )}
    </div>
  );
}

function EntryForm({ d, onChange, options, onSave, onCancel, busy }: { d: EntryDraft; onChange: (d: EntryDraft) => void; options: string[]; onSave: () => void; onCancel: () => void; busy: boolean }) {
  const set = (k: keyof EntryDraft, v: string) => onChange({ ...d, [k]: v } as EntryDraft);
  const listId = `opts-${d.layer}`;
  const input = 'rounded-lg border border-slate-300 px-2 py-1 text-sm focus:border-brand-500 focus:outline-none';
  return (
    <div className="mt-2 rounded-lg border border-brand-200 bg-brand-50/50 p-2" data-testid="v2-stack-form">
      <div className="grid gap-2 sm:grid-cols-[1.4fr_0.8fr_1.4fr_1fr]">
        <label className="text-[11px] text-slate-500">Technology
          <input list={listId} value={d.technology} disabled={d.status === 'open'} onChange={(e) => set('technology', e.target.value)} className={`${input} mt-0.5 w-full`} data-testid="v2-stack-tech" />
          <datalist id={listId}>{options.map((o) => <option key={o} value={o} />)}</datalist>
        </label>
        <label className="text-[11px] text-slate-500">Version
          <input value={d.version} disabled={d.status === 'open'} onChange={(e) => set('version', e.target.value)} className={`${input} mt-0.5 w-full`} placeholder="3.12" />
        </label>
        <label className="text-[11px] text-slate-500">Frameworks and libraries
          <input value={d.extras} disabled={d.status === 'open'} onChange={(e) => set('extras', e.target.value)} className={`${input} mt-0.5 w-full`} placeholder="FastAPI, Pydantic" />
        </label>
        <label className="text-[11px] text-slate-500">Service (optional)
          <input value={d.component} onChange={(e) => set('component', e.target.value)} className={`${input} mt-0.5 w-full`} placeholder="orders-service" />
        </label>
      </div>
      <input value={d.notes} onChange={(e) => set('notes', e.target.value)} className={`${input} mt-2 w-full`} placeholder="Note for the agents, such as “must be FIPS-compliant”" aria-label="Note" />
      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs">
        <label className="inline-flex items-center gap-1"><input type="radio" checked={d.status === 'pinned'} onChange={() => set('status', 'pinned')} />Pin it (a decision)</label>
        <label className="inline-flex items-center gap-1"><input type="radio" checked={d.status === 'open'} onChange={() => set('status', 'open')} data-testid="v2-stack-open" />Leave open for the Technical Architect</label>
        <span className="ml-auto flex gap-2">
          <button type="button" onClick={onCancel} className="rounded-lg border border-slate-300 bg-white px-3 py-1 font-semibold text-slate-700">Cancel</button>
          <button type="button" onClick={onSave} disabled={busy} data-testid="v2-stack-save" className="rounded-lg bg-brand-600 px-3 py-1 font-semibold text-white hover:bg-brand-700 disabled:opacity-50">{busy ? 'Saving…' : 'Save'}</button>
        </span>
      </div>
    </div>
  );
}
