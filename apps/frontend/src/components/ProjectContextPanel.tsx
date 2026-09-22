import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { api } from '../api/client';

/**
 * Project Context workspace: the two human-authored inputs that shape
 * every generation —
 *   📖 Canon    — binding rules, decisions, glossary, constraints
 *   🧱 Formwork — output templates mapped to artifact type + format
 * Both are injected into agent prompts; the Preview tab shows the exact text
 * the agents receive.
 */

interface CanonEntry {
  id: string;
  category: 'rule' | 'decision' | 'glossary' | 'constraint' | 'preference';
  priority: 'must' | 'should' | 'context';
  stage: number | null;
  title: string;
  body: string;
  active: boolean;
  createdAt: string;
}

interface Formwork {
  id: string;
  scope: 'project' | 'platform';
  artefactType: string;
  outputFormat: string;
  name: string;
  template: string;
  analysis: {
    sections?: string[];
    placeholders?: string[];
    jsonKeys?: string[];
    lineCount?: number;
    hasTables?: boolean;
  };
}

const CATEGORIES = ['rule', 'decision', 'glossary', 'constraint', 'preference'] as const;
const PRIORITIES = ['must', 'should', 'context'] as const;
const FORMATS = ['markdown', 'json', 'yaml', 'xml', 'html', 'text'] as const;
const COMMON_TYPES = ['PRD', 'HLD', 'LLD', 'ADR', 'TEST_STRATEGY', 'RTM', 'USER_STORY', 'EPIC', 'OPENAPI'];

const PRIORITY_CHIP: Record<string, string> = {
  must: 'bg-red-100 text-red-700',
  should: 'bg-amber-100 text-amber-700',
  context: 'bg-slate-100 text-slate-600',
};

export default function ProjectContextPanel({ projectId, techStack, onClose }: { projectId: string; techStack?: string; onClose: () => void }) {
  const qc = useQueryClient();
  const [tab, setTab] = useState<'canon' | 'formwork' | 'preview'>('canon');
  const [draft, setDraft] = useState({
    title: '', body: '', category: 'rule' as CanonEntry['category'],
    priority: 'must' as CanonEntry['priority'], stage: '' as string,
  });
  const [fw, setFw] = useState({ artefactType: 'HLD', outputFormat: 'markdown', name: '', template: '' });
  const [error, setError] = useState('');

  const canon = useQuery({
    queryKey: ['canon', projectId],
    queryFn: () => api.get<{ entries: CanonEntry[]; canAuthor: boolean }>(`/api/projects/${projectId}/canon`),
  });
  const formworks = useQuery({
    queryKey: ['formworks', projectId],
    queryFn: () => api.get<{ formworks: Formwork[] }>(`/api/projects/${projectId}/formworks`),
  });
  const preview = useQuery({
    queryKey: ['canon-preview', projectId],
    queryFn: () => api.get<{ canonBlock: string; formworkBlock: string; chars: number }>(
      `/api/projects/${projectId}/canon/preview`,
    ),
    enabled: tab === 'preview',
  });

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['canon', projectId] });
    void qc.invalidateQueries({ queryKey: ['canon-preview', projectId] });
    void qc.invalidateQueries({ queryKey: ['formworks', projectId] });
  };

  const addEntry = useMutation({
    mutationFn: () =>
      api.post(`/api/projects/${projectId}/canon`, {
        ...draft, stage: draft.stage === '' ? null : Number(draft.stage),
      }),
    onSuccess: () => {
      setDraft({ ...draft, title: '', body: '' });
      setError('');
      invalidate();
    },
    onError: (e) => setError(e instanceof Error ? e.message : 'Could not save entry'),
  });

  const toggleEntry = useMutation({
    mutationFn: (e: CanonEntry) =>
      fetch(`/api/projects/${projectId}/canon/${e.id}`, {
        method: 'PATCH', credentials: 'include',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ active: !e.active }),
      }).then((r) => r.json()),
    onSuccess: invalidate,
  });

  const removeEntry = useMutation({
    mutationFn: (id: string) =>
      fetch(`/api/projects/${projectId}/canon/${id}`, { method: 'DELETE', credentials: 'include' })
        .then((r) => r.json()),
    onSuccess: invalidate,
  });

  const uploadFormwork = useMutation({
    mutationFn: () => api.post(`/api/projects/${projectId}/formworks`, fw),
    onSuccess: () => {
      setFw({ ...fw, name: '', template: '' });
      setError('');
      invalidate();
    },
    onError: (e) => setError(e instanceof Error ? e.message : 'Could not publish formwork'),
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const canAuthor = canon.data?.canAuthor ?? false;

  async function onFile(file: File) {
    const text = await file.text();
    setFw((f) => ({ ...f, template: text, name: f.name || file.name }));
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 p-4" onClick={onClose}>
      <div
        className="flex max-h-[94vh] w-full max-w-4xl flex-col rounded-2xl bg-slate-50 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-200 bg-white px-5 py-3">
          <div>
            <div className="text-sm font-bold text-slate-800">📖 Project Context</div>
            <div className="text-xs text-slate-500">
              Canon (binding rules) and Formwork (output templates) — injected into every agent run
              {!canAuthor && ' · read-only for your role'}
            </div>
          </div>
          <button onClick={onClose} className="rounded px-2 py-1 text-slate-400 hover:bg-slate-100">✕</button>
        </div>

        {techStack && (
          <div className="border-b border-slate-200 bg-brand-50 px-5 py-2.5">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[10px] font-semibold uppercase tracking-wide text-brand-700">Technology stack</span>
              <span className="rounded-full bg-brand-600 px-2.5 py-0.5 text-xs font-semibold text-white">{techStack}</span>
            </div>
            <div className="mt-1 text-[11px] text-slate-500">
              Pinned to the project — every phase, custom stage and agent run targets this stack.
            </div>
          </div>
        )}

        <div className="flex gap-1 border-b border-slate-200 bg-white px-5 py-2">
          {([['canon', `📖 Canon (${canon.data?.entries.length ?? 0})`],
             ['formwork', `🧱 Formwork (${formworks.data?.formworks.length ?? 0})`],
             ['preview', '👁 Prompt preview']] as const).map(([id, label]) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={`rounded-lg px-3 py-1 text-xs font-semibold ${
                tab === id ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-500'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
          {error && <div className="rounded bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>}

          {/* ---------------- Canon ---------------- */}
          {tab === 'canon' && (
            <>
              {canAuthor && (
                <div className="rounded-xl border border-slate-200 bg-white p-3">
                  <div className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                    New canon entry
                  </div>
                  <input
                    className="mb-1.5 w-full rounded border border-slate-300 px-2 py-1 text-sm"
                    placeholder="Title — e.g. 'All services emit OpenTelemetry traces'"
                    value={draft.title}
                    onChange={(e) => setDraft({ ...draft, title: e.target.value })}
                  />
                  <textarea
                    className="mb-1.5 w-full rounded border border-slate-300 px-2 py-1 text-sm"
                    rows={3}
                    placeholder="The rule, decision or definition the agents must honour…"
                    value={draft.body}
                    onChange={(e) => setDraft({ ...draft, body: e.target.value })}
                  />
                  <div className="flex flex-wrap items-center gap-1.5">
                    <select
                      className="rounded border border-slate-300 px-2 py-1 text-xs"
                      value={draft.category}
                      onChange={(e) => setDraft({ ...draft, category: e.target.value as CanonEntry['category'] })}
                    >
                      {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
                    </select>
                    <select
                      className="rounded border border-slate-300 px-2 py-1 text-xs"
                      value={draft.priority}
                      onChange={(e) => setDraft({ ...draft, priority: e.target.value as CanonEntry['priority'] })}
                    >
                      {PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
                    </select>
                    <select
                      className="rounded border border-slate-300 px-2 py-1 text-xs"
                      value={draft.stage}
                      onChange={(e) => setDraft({ ...draft, stage: e.target.value })}
                    >
                      <option value="">all stages</option>
                      {[1, 2, 3, 4, 5, 6].map((s) => <option key={s} value={s}>stage {s} only</option>)}
                    </select>
                    <button
                      onClick={() => addEntry.mutate()}
                      disabled={addEntry.isPending || draft.title.trim().length < 3 || !draft.body.trim()}
                      className="ml-auto rounded-lg bg-brand-600 px-3 py-1 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-40"
                    >
                      Add to Canon
                    </button>
                  </div>
                </div>
              )}

              {(canon.data?.entries ?? []).length === 0 && (
                <div className="rounded-xl bg-white p-4 text-center text-xs text-slate-400">
                  No canon entries yet. Add rules, decisions or glossary terms and every agent will
                  honour them from the next run onward.
                </div>
              )}
              {(canon.data?.entries ?? []).map((e) => (
                <div
                  key={e.id}
                  className={`rounded-xl border bg-white p-3 ${e.active ? 'border-slate-200' : 'border-slate-200 opacity-50'}`}
                >
                  <div className="flex items-center gap-1.5">
                    <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${PRIORITY_CHIP[e.priority]}`}>
                      {e.priority}
                    </span>
                    <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600">{e.category}</span>
                    {e.stage && (
                      <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] text-indigo-600">stage {e.stage}</span>
                    )}
                    <span className="min-w-0 flex-1 truncate text-sm font-semibold text-slate-800">{e.title}</span>
                    {canAuthor && (
                      <>
                        <button
                          onClick={() => toggleEntry.mutate(e)}
                          className="rounded px-1.5 py-0.5 text-[10px] font-semibold text-slate-500 hover:bg-slate-100"
                        >
                          {e.active ? 'disable' : 'enable'}
                        </button>
                        <button
                          onClick={() => removeEntry.mutate(e.id)}
                          className="rounded px-1.5 py-0.5 text-[10px] text-slate-400 hover:bg-red-50 hover:text-red-600"
                        >
                          delete
                        </button>
                      </>
                    )}
                  </div>
                  <div className="mt-1 whitespace-pre-wrap text-xs text-slate-600">{e.body}</div>
                </div>
              ))}
            </>
          )}

          {/* ---------------- Formwork ---------------- */}
          {tab === 'formwork' && (
            <>
              {canAuthor && (
                <div className="rounded-xl border border-slate-200 bg-white p-3">
                  <div className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                    Publish an output template
                  </div>
                  <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
                    <input
                      list="artefact-types"
                      className="w-40 rounded border border-slate-300 px-2 py-1 text-xs font-mono"
                      placeholder="Artifact type"
                      value={fw.artefactType}
                      onChange={(e) => setFw({ ...fw, artefactType: e.target.value.toUpperCase() })}
                    />
                    <datalist id="artefact-types">
                      {COMMON_TYPES.map((t) => <option key={t} value={t} />)}
                    </datalist>
                    <select
                      className="rounded border border-slate-300 px-2 py-1 text-xs"
                      value={fw.outputFormat}
                      onChange={(e) => setFw({ ...fw, outputFormat: e.target.value })}
                    >
                      {FORMATS.map((f) => <option key={f} value={f}>{f}</option>)}
                    </select>
                    <input
                      className="min-w-40 flex-1 rounded border border-slate-300 px-2 py-1 text-xs"
                      placeholder="Template name"
                      value={fw.name}
                      onChange={(e) => setFw({ ...fw, name: e.target.value })}
                    />
                    <label className="cursor-pointer rounded-lg border border-slate-300 px-2 py-1 text-xs font-semibold text-slate-600 hover:bg-slate-50">
                      📎 Attach file
                      <input
                        type="file"
                        className="hidden"
                        accept=".md,.txt,.json,.yaml,.yml,.xml,.html"
                        onChange={(e) => e.target.files?.[0] && void onFile(e.target.files[0])}
                      />
                    </label>
                  </div>
                  <textarea
                    className="mb-1.5 w-full rounded border border-slate-300 px-2 py-1 font-mono text-[11px]"
                    rows={6}
                    placeholder="Paste the template — headings define required sections, {{placeholders}} become fields the agent must fill…"
                    value={fw.template}
                    onChange={(e) => setFw({ ...fw, template: e.target.value })}
                  />
                  <button
                    onClick={() => uploadFormwork.mutate()}
                    disabled={uploadFormwork.isPending || fw.template.trim().length < 20 || !fw.artefactType}
                    className="rounded-lg bg-brand-600 px-3 py-1 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-40"
                  >
                    Analyse &amp; publish formwork
                  </button>
                </div>
              )}

              {(formworks.data?.formworks ?? []).length === 0 && (
                <div className="rounded-xl bg-white p-4 text-center text-xs text-slate-400">
                  No formworks yet. Attach a template and generated documents of that artifact type
                  will follow its structure.
                </div>
              )}
              {(formworks.data?.formworks ?? []).map((f) => (
                <div key={f.id} className="rounded-xl border border-slate-200 bg-white p-3">
                  <div className="flex items-center gap-1.5">
                    <span className="rounded bg-brand-50 px-1.5 py-0.5 text-[10px] font-bold text-brand-700">
                      {f.artefactType}
                    </span>
                    <span className="text-slate-300">→</span>
                    <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600">{f.outputFormat}</span>
                    <span className="min-w-0 flex-1 truncate text-sm text-slate-700">{f.name}</span>
                    <span className={`rounded px-1.5 py-0.5 text-[9px] font-semibold ${
                      f.scope === 'platform' ? 'bg-indigo-50 text-indigo-600' : 'bg-emerald-50 text-emerald-600'
                    }`}>
                      {f.scope}
                    </span>
                  </div>
                  <div className="mt-1 text-[11px] text-slate-500">
                    {f.analysis.sections?.length ? `${f.analysis.sections.length} sections: ${f.analysis.sections.slice(0, 5).join(' → ')}` : 'no headings detected'}
                    {f.analysis.placeholders?.length ? ` · ${f.analysis.placeholders.length} placeholders` : ''}
                    {f.analysis.jsonKeys?.length ? ` · keys: ${f.analysis.jsonKeys.slice(0, 6).join(', ')}` : ''}
                  </div>
                </div>
              ))}
            </>
          )}

          {/* ---------------- Preview ---------------- */}
          {tab === 'preview' && (
            <div className="space-y-2">
              <div className="rounded-lg bg-slate-100 px-3 py-2 text-xs text-slate-600">
                This is the exact text prepended to every agent prompt for this project
                {preview.data ? ` (${preview.data.chars} chars)` : ''}. Empty sections mean nothing is
                being injected yet.
              </div>
              <pre className="overflow-x-auto rounded-lg bg-slate-900 p-3 text-[11px] leading-relaxed text-slate-100">
                {(preview.data?.canonBlock || '(no canon entries)') + '\n\n' +
                 (preview.data?.formworkBlock || '(no formworks)')}
              </pre>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
