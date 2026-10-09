import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';
import { filterPacks, KIND_FILTERS, type PackKind } from '../lib/profile';
import { Pill } from './bits';

interface OrgPack {
  id: string; name: string; description: string; kind: PackKind; kindLabel: string; version: number; baseline: boolean;
  tags: { industries: string[]; regulations: string[]; domains: string[] }; includes: string[]; rules: number; source: 'builtin' | 'org' | 'customised'; hidden: boolean; canRestore: boolean;
}
const SOURCE_WORD = { builtin: 'Sample', org: 'Your organisation', customised: 'Customised sample' } as const;
const NEW_PACK = `id: my-team-standards
name: My team standards
description: What our teams always do.
kind: practice
entries:
  - title: Example rule
    body: Say what must happen and why.
    priority: must
    category: rule
    stage: null
`;
const btn = 'rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:border-brand-400 disabled:opacity-50';
const primary = 'rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-50';

/**
 * Governance -> Organisation -> Rule packs. The built-in library is a set of samples; here an administrator adds the organisation's own packs,
 * edits a sample for everyone, hides the ones that do not apply, and restores. A pack is written as YAML so it can be kept in source control.
 */
export default function OrgPacks() {
  const qc = useQueryClient();
  const [kind, setKind] = useState<PackKind | 'all'>('all');
  const [query, setQuery] = useState('');
  const [editor, setEditor] = useState<{ text: string; title: string } | null>(null);
  const [note, setNote] = useState('');
  const q = useQuery({ queryKey: ['org-packs'], queryFn: () => api.get<{ packs: OrgPack[]; canEdit: boolean }>('/api/org/packs') });
  const refresh = () => { void qc.invalidateQueries({ queryKey: ['org-packs'] }); void qc.invalidateQueries({ queryKey: ['rules'] }); void qc.invalidateQueries({ queryKey: ['rule-advice'] }); };
  const fail = (e: unknown) => setNote(e instanceof Error ? e.message : 'That did not work');
  const save = useMutation({ mutationFn: (text: string) => api.post<{ pack: { name: string; version: number } }>('/api/org/packs/import', { text }), onSuccess: (r) => { setNote(`Saved ${r.pack.name} (version ${r.pack.version}).`); setEditor(null); refresh(); }, onError: fail });
  const hide = useMutation({ mutationFn: (id: string) => api.post(`/api/org/packs/${id}/hide`), onSuccess: () => { setNote(''); refresh(); }, onError: fail });
  const restore = useMutation({ mutationFn: (id: string) => api.del<{ result: string }>(`/api/org/packs/${id}`), onSuccess: (r) => { setNote(r.result === 'restored' ? 'Restored the sample.' : 'Removed.'); refresh(); }, onError: fail });
  const edit = async (id: string, name: string) => {
    const res = await fetch(`/api/org/packs/${id}/export`, { credentials: 'include' });
    if (!res.ok) { setNote('Could not open that pack'); return; }
    setEditor({ text: await res.text(), title: `Edit ${name}` });
  };
  if (!q.data) return <div className="animate-pulse text-sm text-slate-400">Loading…</div>;
  const { packs, canEdit } = q.data;
  const shown = filterPacks(packs.map((p) => ({ ...p, count: p.rules, alreadyHave: 0, includes: p.includes.map((i) => ({ id: i, name: i })), stages: [] })), kind, query).map((p) => packs.find((x) => x.id === p.id)!);

  return (
    <div data-testid="v2-org-packs-panel">
      <p className="mb-3 text-sm text-slate-600">The library projects choose from. Packs marked <b>Sample</b> ship with the platform; change one to make it yours, or hide the ones that do not apply. Projects pick up a new version when they next add the pack.</p>
      <div className="mb-2 flex flex-wrap items-center gap-1.5" role="tablist" aria-label="Kind of pack">
        {KIND_FILTERS.map((f) => <button key={f.id} role="tab" aria-selected={kind === f.id} onClick={() => setKind(f.id)}
          className={`rounded-full px-3 py-1 text-xs font-medium ${kind === f.id ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>{f.label}</button>)}
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search packs…" aria-label="Search packs" className="ml-auto min-w-[12rem] rounded-lg border border-slate-300 px-2 py-1 text-sm focus:border-brand-500 focus:outline-none" />
        {canEdit && <button type="button" className={primary} onClick={() => setEditor({ text: NEW_PACK, title: 'New pack' })} data-testid="v2-org-pack-new">New pack</button>}
      </div>
      {note && <div className="mb-2 text-xs text-slate-600" role="status" data-testid="v2-org-pack-note">{note}</div>}
      {editor && (
        <div className="mb-3 rounded-xl border border-brand-300 bg-white p-3" data-testid="v2-org-pack-editor">
          <div className="mb-1 text-sm font-semibold text-slate-800">{editor.title}</div>
          <p className="mb-1.5 text-xs text-slate-500">A pack is YAML. <code>kind</code> is practice, regulation, industry or bundle; a bundle lists <code>includes</code> (pack ids) instead of rules. Tags (<code>tags: industries / regulations / domains</code>) decide when it is recommended.</p>
          <textarea value={editor.text} onChange={(e) => setEditor({ ...editor, text: e.target.value })} rows={14} spellCheck={false} aria-label="Pack file" data-testid="v2-org-pack-text"
            className="w-full rounded-lg border border-slate-300 p-2 font-mono text-xs focus:border-brand-500 focus:outline-none" />
          <div className="mt-2 flex gap-2">
            <button type="button" className={primary} disabled={save.isPending || !editor.text.trim()} onClick={() => save.mutate(editor.text)} data-testid="v2-org-pack-save">Save pack</button>
            <button type="button" className={btn} onClick={() => setEditor(null)}>Cancel</button>
          </div>
        </div>
      )}
      <ul className="divide-y divide-slate-200 rounded-xl border border-slate-300 bg-white" data-testid="v2-org-pack-list">
        {shown.map((p) => (
          <li key={p.id} className={`px-3 py-2 ${p.hidden ? 'opacity-60' : ''}`} data-testid={`v2-org-pack-${p.id}`}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-semibold text-slate-900">{p.name}</span>
              <Pill tone={p.kind === 'bundle' ? 'brand' : 'slate'}>{p.kindLabel}</Pill>
              <Pill>{p.rules} rules</Pill><Pill>v{p.version}</Pill>
              {p.source !== 'builtin' && <Pill tone="amber">{SOURCE_WORD[p.source]}</Pill>}
              {p.hidden && <Pill tone="red">Hidden</Pill>}
              {canEdit && (
                <span className="ml-auto flex gap-3 text-xs font-semibold text-brand-700">
                  {!p.hidden && <button type="button" className="hover:underline" onClick={() => void edit(p.id, p.name)}>{p.source === 'builtin' ? 'Customise' : 'Edit'}</button>}
                  {!p.hidden && <a className="hover:underline" href={`/api/org/packs/${p.id}/export`} download={`${p.id}.yaml`}>Export</a>}
                  {!p.hidden && <button type="button" className="hover:underline" onClick={() => hide.mutate(p.id)}>Hide</button>}
                  {(p.hidden || p.source === 'org' || p.canRestore) && <button type="button" className="text-slate-600 hover:underline" onClick={() => restore.mutate(p.id)}>{p.source === 'org' ? 'Delete' : 'Restore sample'}</button>}
                </span>
              )}
            </div>
            <p className="text-xs text-slate-500">{p.description}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}
