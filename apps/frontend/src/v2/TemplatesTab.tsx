import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';
import { COMMON_TYPES, FORMATS, usedBy, type Template, type TemplateSuggestion } from '../lib/rules';
import { Pill } from './bits';

const input = 'rounded-lg border border-slate-300 px-2 py-1 text-sm focus:border-brand-500 focus:outline-none';
const btn = 'rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:border-brand-400 disabled:opacity-50';
const primary = 'rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-50';

/**
 * Output templates: the layout each kind of document follows. Drop a file and the type and format are worked out for you to confirm.
 * With no `projectId` this is the platform-wide library (administrators).
 */
export default function TemplatesTab({ projectId, canEdit }: { projectId?: string; canEdit: boolean }) {
  const qc = useQueryClient();
  const base = projectId ? `/api/projects/${projectId}/formworks` : '/api/formworks';
  const key = ['formworks-v2', projectId ?? 'platform'];
  const [draft, setDraft] = useState<{ name: string; template: string; artefactType: string; outputFormat: string; candidates: string[]; sections: string[]; placeholders: string[] } | null>(null);
  const [error, setError] = useState('');
  const [open, setOpen] = useState<string | null>(null);
  const list = useQuery({ queryKey: key, queryFn: () => api.get<{ formworks: Template[] }>(base) });
  const refresh = () => { void qc.invalidateQueries({ queryKey: key }); if (projectId) { void qc.invalidateQueries({ queryKey: ['formworks', projectId] }); void qc.invalidateQueries({ queryKey: ['context-preview', projectId] }); } };
  const suggest = useMutation({
    mutationFn: (f: { name: string; template: string }) => api.post<TemplateSuggestion>(projectId ? `/api/projects/${projectId}/formworks/suggest` : '/api/formworks/suggest', f),
    onSuccess: (s, f) => setDraft({ name: s.name, template: f.template, artefactType: s.artefactType ?? '', outputFormat: s.outputFormat, candidates: s.candidates, sections: s.sections, placeholders: s.placeholders }),
    onError: (e) => setError(e instanceof Error ? e.message : 'Could not read that file'),
  });
  const publish = useMutation({
    mutationFn: () => api.post(base, { artefactType: draft!.artefactType, outputFormat: draft!.outputFormat, name: draft!.name, template: draft!.template }),
    onSuccess: () => { setDraft(null); setError(''); refresh(); }, onError: (e) => setError(e instanceof Error ? e.message : 'Could not publish'),
  });
  const retire = useMutation({ mutationFn: (id: string) => api.del(`/api/formworks/${id}`), onSuccess: refresh, onError: (e) => setError(e instanceof Error ? e.message : 'Could not retire') });
  if (!list.data) return <div className="animate-pulse text-sm text-slate-400">Loading…</div>;
  const rows = list.data.formworks;

  return (
    <div data-testid="v2-templates-tab">
      <p className="mb-3 text-sm text-slate-600">
        A template fixes the sections, headings and format a kind of document follows. {projectId ? 'A project template takes the place of the platform one for that type. ' : 'Platform templates apply to every project that has not set its own. '}
        You can also choose a template per document in a stage's plan.
      </p>
      {canEdit && !draft && (
        <label className="mb-3 flex cursor-pointer flex-col items-center gap-1 rounded-xl border-2 border-dashed border-slate-300 bg-white px-4 py-5 text-center text-sm text-slate-600 hover:border-brand-400"
          onDragOver={(e) => e.preventDefault()} onDrop={async (e) => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) suggest.mutate({ name: f.name, template: await f.text() }); }} data-testid="v2-template-drop">
          <span className="font-semibold text-slate-800">{suggest.isPending ? 'Reading the file…' : 'Drop a template here, or choose a file'}</span>
          <span className="text-xs text-slate-500">Markdown, JSON, YAML, XML, HTML or text. We work out what it is for; you confirm.</span>
          <input type="file" className="hidden" accept=".md,.markdown,.txt,.json,.yaml,.yml,.xml,.html,.htm" data-testid="v2-template-file"
            onChange={async (e) => { const f = e.target.files?.[0]; if (f) suggest.mutate({ name: f.name, template: await f.text() }); e.target.value = ''; }} />
        </label>
      )}
      {draft && (
        <div className="mb-3 rounded-xl border border-brand-200 bg-brand-50/40 p-3" data-testid="v2-template-confirm">
          <div className="mb-2 text-sm font-semibold text-slate-800">{draft.artefactType ? <>This looks like a <b>{draft.artefactType}</b> template</> : 'Which document is this template for?'}</div>
          <div className="grid gap-2 sm:grid-cols-3">
            <label className="text-[11px] text-slate-500">Name<input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} className={`${input} mt-0.5 w-full`} /></label>
            <label className="text-[11px] text-slate-500">Document type
              <input list="tpl-types" value={draft.artefactType} onChange={(e) => setDraft({ ...draft, artefactType: e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, '_') })} className={`${input} mt-0.5 w-full`} data-testid="v2-template-type" />
              <datalist id="tpl-types">{[...draft.candidates, ...COMMON_TYPES.filter((t) => !draft.candidates.includes(t))].map((t) => <option key={t} value={t} />)}</datalist>
            </label>
            <label className="text-[11px] text-slate-500">Format
              <select value={draft.outputFormat} onChange={(e) => setDraft({ ...draft, outputFormat: e.target.value })} className={`${input} mt-0.5 w-full`}>{FORMATS.map((f) => <option key={f} value={f}>{f}</option>)}</select>
            </label>
          </div>
          {draft.candidates.length > 1 && <div className="mt-1 text-[11px] text-slate-500">Other likely types: {draft.candidates.slice(1).map((c) => <button key={c} type="button" className="mr-1 underline" onClick={() => setDraft({ ...draft, artefactType: c })}>{c}</button>)}</div>}
          <div className="mt-2 grid gap-3 text-xs text-slate-600 sm:grid-cols-2">
            <div><div className="font-semibold text-slate-700">Sections found ({draft.sections.length})</div>{draft.sections.length ? <ol className="list-decimal pl-4">{draft.sections.slice(0, 12).map((s, i) => <li key={i}>{s}</li>)}</ol> : <span className="text-slate-400">None (a flat file)</span>}</div>
            <div><div className="font-semibold text-slate-700">Placeholders to fill ({draft.placeholders.length})</div>{draft.placeholders.length ? <div className="flex flex-wrap gap-1">{draft.placeholders.slice(0, 14).map((p) => <code key={p} className="rounded bg-white px-1">{p}</code>)}</div> : <span className="text-slate-400">None</span>}</div>
          </div>
          {error && <div className="mt-2 text-xs text-red-700">{error}</div>}
          <div className="mt-2 flex justify-end gap-2"><button type="button" className={btn} onClick={() => { setDraft(null); setError(''); }}>Cancel</button>
            <button type="button" className={primary} disabled={publish.isPending || !draft.artefactType || draft.name.trim().length < 2} onClick={() => publish.mutate()} data-testid="v2-template-publish">{publish.isPending ? 'Publishing…' : 'Publish template'}</button></div>
        </div>
      )}
      {error && !draft && <div className="mb-2 text-xs text-red-700">{error}</div>}
      {rows.length === 0 && <div className="rounded-lg bg-slate-100 p-4 text-center text-sm text-slate-500">No templates yet. Documents follow the platform's standard layout.</div>}
      <ul className="space-y-2" data-testid="v2-templates-list">
        {rows.map((t) => (
          <li key={t.id} className="rounded-xl border border-slate-200 bg-white px-3 py-2.5" data-testid="v2-template">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-semibold text-slate-900">{t.name}</span>
              <Pill tone="brand">{t.artefactType} → {t.outputFormat}</Pill>
              <Pill tone={t.scope === 'platform' ? 'slate' : 'green'}>{t.scope === 'platform' ? 'Platform' : 'This project'}</Pill>
              {t.overrides && <Pill tone="amber" title="A platform template exists for this type; this project's takes its place">Overrides the platform template</Pill>}
              {projectId && <span className="text-[11px] text-slate-500">{usedBy(t)}</span>}
              {(t.versions ?? 1) > 1 && <span className="text-[11px] text-slate-400">{t.versions} versions</span>}
              <span className="ml-auto flex gap-2 text-xs">
                <button type="button" className="font-semibold text-brand-700 hover:underline" onClick={() => setOpen(open === t.id ? null : t.id)} aria-expanded={open === t.id}>{open === t.id ? 'Hide' : 'Preview'}</button>
                {canEdit && (t.scope === 'project') === Boolean(projectId) && <button type="button" className="text-slate-400 hover:text-red-600" onClick={() => { if (window.confirm(`Retire “${t.name}”? Documents go back to the standard layout.`)) retire.mutate(t.id); }}>Retire</button>}
              </span>
            </div>
            {open === t.id && (
              <div className="mt-2 grid gap-3 text-xs text-slate-600 md:grid-cols-[1fr_1.4fr]">
                <div><div className="font-semibold text-slate-700">Sections</div>{(t.analysis.sections ?? []).length ? <ol className="list-decimal pl-4">{t.analysis.sections!.map((s, i) => <li key={i}>{s}</li>)}</ol> : <span className="text-slate-400">None</span>}
                  {(t.analysis.placeholders ?? []).length > 0 && <><div className="mt-2 font-semibold text-slate-700">Placeholders</div><div className="flex flex-wrap gap-1">{t.analysis.placeholders!.slice(0, 20).map((p) => <code key={p} className="rounded bg-slate-100 px-1">{p}</code>)}</div></>}</div>
                <pre className="max-h-56 overflow-auto rounded-lg bg-slate-100 p-2 font-mono text-[11px] text-slate-700">{t.template.slice(0, 4000)}</pre>
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
