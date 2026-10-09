import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';
import { render, type StackPreset } from '../lib/stackConfig';
import { CATEGORIES, PRIORITIES, PRIORITY_TONE, PRIORITY_WORD, STAGE_NAMES, type Rule, type RuleCategory, type RulePriority } from '../lib/rules';
import { Pill } from './bits';
import TemplatesTab from './TemplatesTab';

const input = 'rounded-lg border border-slate-300 px-2 py-1 text-sm focus:border-brand-500 focus:outline-none';
const primary = 'rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-50';

/** Governance -> Organisation: rules every project inherits, stack presets, and platform-wide templates. Administrators edit; everyone can read. */
export default function OrgContext() {
  const [section, setSection] = useState<'rules' | 'presets' | 'templates'>('rules');
  const rules = useQuery({ queryKey: ['org-rules'], queryFn: () => api.get<{ entries: Rule[]; canEdit: boolean }>('/api/org/rules') });
  const canEdit = rules.data?.canEdit ?? false;
  return (
    <div data-testid="v2-org-context">
      <p className="mb-3 text-sm text-slate-600">Set once, used by every project. {canEdit ? '' : 'Only administrators change these.'} A project can opt out of an organisation rule, with a reason that is recorded.</p>
      <div className="mb-3 flex gap-1" role="tablist">
        {([['rules', 'Rules'], ['presets', 'Stack presets'], ['templates', 'Templates']] as const).map(([id, label]) => (
          <button key={id} role="tab" aria-selected={section === id} onClick={() => setSection(id)} data-testid={`v2-org-${id}`}
            className={`rounded-full px-3 py-1 text-xs font-medium ${section === id ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>{label}</button>
        ))}
      </div>
      {section === 'rules' && <OrgRules entries={rules.data?.entries ?? []} canEdit={canEdit} />}
      {section === 'presets' && <OrgPresets />}
      {section === 'templates' && <TemplatesTab canEdit={canEdit} />}
    </div>
  );
}

function OrgRules({ entries, canEdit }: { entries: Rule[]; canEdit: boolean }) {
  const qc = useQueryClient();
  const [d, setD] = useState({ title: '', body: '', category: 'rule' as RuleCategory, priority: 'must' as RulePriority, stage: null as number | null });
  const [error, setError] = useState('');
  const refresh = () => void qc.invalidateQueries({ queryKey: ['org-rules'] });
  const add = useMutation({ mutationFn: () => api.post('/api/org/rules', d), onSuccess: () => { setD({ ...d, title: '', body: '' }); setError(''); refresh(); }, onError: (e) => setError(e instanceof Error ? e.message : 'Could not save') });
  const patch = useMutation({ mutationFn: (v: { id: string; body: Record<string, unknown> }) => api.patch(`/api/org/rules/${v.id}`, v.body), onSuccess: refresh });
  const remove = useMutation({ mutationFn: (id: string) => api.del(`/api/org/rules/${id}`), onSuccess: refresh });
  return (
    <div>
      {canEdit && (
        <div className="mb-3 space-y-2 rounded-xl border border-slate-300 bg-white p-3">
          <input value={d.title} onChange={(e) => setD({ ...d, title: e.target.value })} placeholder="Rule title" aria-label="Organisation rule title" className={`${input} w-full`} data-testid="v2-org-rule-title" />
          <textarea value={d.body} onChange={(e) => setD({ ...d, body: e.target.value })} rows={2} placeholder="The rule, and why" aria-label="Organisation rule text" className={`${input} w-full`} data-testid="v2-org-rule-body" />
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <select aria-label="Priority" value={d.priority} onChange={(e) => setD({ ...d, priority: e.target.value as RulePriority })} className={input}>{PRIORITIES.map((p) => <option key={p} value={p}>{PRIORITY_WORD[p]}</option>)}</select>
            <select aria-label="Kind" value={d.category} onChange={(e) => setD({ ...d, category: e.target.value as RuleCategory })} className={input}>{CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}</select>
            <select aria-label="Applies to" value={d.stage ?? ''} onChange={(e) => setD({ ...d, stage: e.target.value === '' ? null : Number(e.target.value) })} className={input}>
              <option value="">Every stage</option>{Object.entries(STAGE_NAMES).map(([n, name]) => <option key={n} value={n}>Stage {n} · {name}</option>)}
            </select>
            {error && <span className="text-red-700">{error}</span>}
            <button type="button" className={`${primary} ml-auto`} disabled={d.title.trim().length < 3 || !d.body.trim() || add.isPending} onClick={() => add.mutate()} data-testid="v2-org-rule-add">Add organisation rule</button>
          </div>
        </div>
      )}
      {entries.length === 0 && <div className="rounded-lg bg-slate-100 p-4 text-center text-sm text-slate-500">No organisation rules yet.</div>}
      <ul className="space-y-1.5">
        {entries.map((r) => (
          <li key={r.id} className={`rounded-lg border bg-white px-3 py-2 ${r.active ? 'border-slate-200' : 'border-dashed border-slate-300 opacity-60'}`}>
            <div className="flex flex-wrap items-center gap-2"><Pill tone={PRIORITY_TONE[r.priority]}>{PRIORITY_WORD[r.priority]}</Pill><span className="text-[11px] text-slate-500">{r.stage ? `Stage ${r.stage}` : 'Every stage'}</span><span className="text-sm font-medium text-slate-900">{r.title}</span>
              {canEdit && <span className="ml-auto flex gap-2 text-xs"><button type="button" className="text-slate-500 hover:text-slate-800" onClick={() => patch.mutate({ id: r.id, body: { active: !r.active } })}>{r.active ? 'Turn off' : 'Turn on'}</button><button type="button" className="text-slate-400 hover:text-red-600" onClick={() => { if (window.confirm(`Delete “${r.title}”?`)) remove.mutate(r.id); }}>Delete</button></span>}
            </div>
            <p className="mt-0.5 text-xs text-slate-600">{r.body}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

function OrgPresets() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['org-presets'], queryFn: () => api.get<{ presets: Array<StackPreset & { builtIn: boolean }>; canEdit: boolean }>('/api/org/stack-presets') });
  const [name, setName] = useState('');
  const [lines, setLines] = useState('hosting: AWS\nbackend: Python 3.12 + FastAPI\ndatabase: PostgreSQL 16');
  const [error, setError] = useState('');
  const refresh = () => void qc.invalidateQueries({ queryKey: ['org-presets'] });
  const add = useMutation({
    mutationFn: () => {
      const layers = lines.split('\n').map((l) => l.trim()).filter(Boolean).map((l) => {
        const [layer, ...rest] = l.split(':');
        const parts = rest.join(':').split('+').map((x) => x.trim()).filter(Boolean);
        const m = /^(.*?)\s+(v?\d[\w.\-]*)$/.exec(parts[0] ?? '');
        return { layer: (layer ?? '').trim().toLowerCase(), technology: m ? m[1] : parts[0] ?? '', version: m ? m[2] : '', extras: parts.slice(1) };
      });
      return api.post('/api/org/stack-presets', { name, description: '', layers });
    },
    onSuccess: () => { setName(''); setError(''); refresh(); }, onError: (e) => setError(e instanceof Error ? e.message : 'Could not save'),
  });
  const remove = useMutation({ mutationFn: (id: string) => api.del(`/api/org/stack-presets/${id}`), onSuccess: refresh });
  const canEdit = q.data?.canEdit ?? false;
  return (
    <div data-testid="v2-org-presets-list">
      <p className="mb-3 text-xs text-slate-500">A preset pins a set of stack layers on a project in one click (Project Context → Stack). The built-in ones are starting points.</p>
      <ul className="mb-3 space-y-1.5">
        {(q.data?.presets ?? []).map((p) => (
          <li key={p.id} className="rounded-lg border border-slate-200 bg-white px-3 py-2">
            <div className="flex items-center gap-2"><span className="text-sm font-semibold text-slate-900">{p.name}</span>{p.builtIn ? <Pill>Built in</Pill> : <Pill tone="brand">Organisation</Pill>}
              {canEdit && !p.builtIn && <button type="button" className="ml-auto text-xs text-slate-400 hover:text-red-600" onClick={() => { if (window.confirm(`Remove “${p.name}”?`)) remove.mutate(p.id); }}>Remove</button>}</div>
            {p.description && <p className="text-xs text-slate-500">{p.description}</p>}
            <p className="mt-0.5 text-xs text-slate-600">{(p.layers as Array<{ layer: string; technology: string; version?: string; extras?: string[] }>).map((l) => `${l.layer}: ${render({ technology: l.technology, version: l.version ?? '', extras: l.extras ?? [] })}`).join(' · ')}</p>
          </li>
        ))}
      </ul>
      {canEdit && (
        <div className="space-y-2 rounded-xl border border-slate-300 bg-white p-3">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Preset name, for example “Our standard AWS stack”" aria-label="Preset name" className={`${input} w-full`} />
          <textarea value={lines} onChange={(e) => setLines(e.target.value)} rows={5} aria-label="Preset layers" className={`${input} w-full font-mono`} />
          <p className="text-[11px] text-slate-400">One layer per line: <code>layer: technology version + framework</code>. Layers: frontend, backend, database, cache, messaging, search, hosting, compute, iac, cicd, observability, identity.</p>
          {error && <div className="text-xs text-red-700">{error}</div>}
          <div className="flex justify-end"><button type="button" className={primary} disabled={name.trim().length < 3 || add.isPending} onClick={() => add.mutate()}>Add preset</button></div>
        </div>
      )}
    </div>
  );
}
