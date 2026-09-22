import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import CodeView from './CodeView';
import DiagramWorkbench from './DiagramWorkbench';
import { MarkdownDoc, diagramKindOf, resolveViewer, type ViewerContext } from './viewerRegistry';

interface RepairResult {
  ok: boolean;
  changed?: boolean;
  version?: number;
  via?: string;
  issues?: string[];
  message?: string;
}

interface ArtefactDetail {
  artefact: {
    id: string;
    type: string;
    title: string;
    content: string;
    url: string | null;
    phase: number;
    storageKey?: string | null;
    storageMode?: string | null;
    canEdit?: boolean;
  };
}

function extOf(storageKey?: string | null): string {
  if (!storageKey) return '';
  const dot = storageKey.lastIndexOf('.');
  return dot > 0 ? storageKey.slice(dot).toLowerCase() : '';
}

function TabBar({ tabs, active, onSelect }: { tabs: string[]; active: string; onSelect: (t: string) => void }) {
  return (
    <div className="mb-3 flex gap-1">
      {tabs.map((t) => (
        <button
          key={t}
          onClick={() => onSelect(t)}
          className={`rounded-lg px-3 py-1 text-xs font-semibold ${
            t === active ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-500 hover:bg-slate-200'
          }`}
        >
          {t}
        </button>
      ))}
    </div>
  );
}

/**
 * File-first artifact viewer: every artifact opens with the
 * right viewer, chosen by the extensible viewer plugin registry — rendered
 * mermaid/PlantUML diagrams, markdown, JSON, CSV tables and language-aware
 * code, each with a Source tab where relevant — plus a Download action.
 * Supporting a new file type is a single entry in viewerRegistry, not a change
 * here.
 */
export default function ArtifactViewer({
  projectId,
  artefactId,
  onClose,
  canRepair = false,
}: {
  projectId: string;
  artefactId: string;
  onClose: () => void;
  /** Show diagram Fix syntax / Regenerate actions (stage writers only). */
  canRepair?: boolean;
}) {
  const qc = useQueryClient();
  const [tab, setTab] = useState<string>('');
  const [repairing, setRepairing] = useState<null | 'fix' | 'regenerate'>(null);
  const [repairMsg, setRepairMsg] = useState('');
  // In-place manual edit: stage writers edit the source and save instantly.
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState('');
  const detail = useQuery({
    queryKey: ['artefact', projectId, artefactId],
    queryFn: () => api.get<ArtefactDetail>(`/api/projects/${projectId}/artefacts/${artefactId}`),
  });

  type SaveResult = { ok: boolean; version?: number; masked?: string[] };

  async function persist(
    label: string, call: () => Promise<SaveResult>, { closeEditor = true } = {},
  ) {
    setSaving(true);
    setSaveMsg('');
    try {
      const r = await call();
      setSaveMsg(
        `✓ ${label}${r.version ? ` (v${r.version})` : ''}`
        + (r.masked && r.masked.length ? ` — ${r.masked.length} secret/PII value(s) were masked` : ''),
      );
      if (closeEditor) setEditing(false);
      await qc.invalidateQueries({ queryKey: ['artefact', projectId, artefactId] });
      void qc.invalidateQueries({ queryKey: ['artefacts', projectId] });
    } catch (err) {
      setSaveMsg(err instanceof Error ? err.message : `${label} failed`);
    } finally {
      setSaving(false);
    }
  }

  const saveContent = (content: string, closeEditor = true) =>
    persist('Saved', () => api.put<SaveResult>(`/api/projects/${projectId}/artefacts/${artefactId}`, { content }),
      { closeEditor });

  const replaceFile = (file: File) =>
    persist('Replaced', () => api.upload<SaveResult>(`/api/projects/${projectId}/artefacts/${artefactId}/replace`, file));

  // repair a broken diagram in place. 'fix' preserves content and only
  // corrects syntax; 'regenerate' lets the model redraw it. On success the
  // artifact query is invalidated so the viewer re-renders the new version.
  async function repair(mode: 'fix' | 'regenerate') {
    setRepairing(mode);
    setRepairMsg('');
    try {
      const r = await api.post<RepairResult>(
        `/api/projects/${projectId}/artefacts/${artefactId}/repair`, { mode },
      );
      if (r.ok) {
        setRepairMsg(
          r.changed === false
            ? (r.message ?? 'The diagram already parses; nothing to fix.')
            : `✓ ${mode === 'regenerate' ? 'Regenerated' : 'Fixed'} (v${r.version}${r.via === 'llm' ? ' · AI' : ''})`,
        );
        await qc.invalidateQueries({ queryKey: ['artefact', projectId, artefactId] });
        void qc.invalidateQueries({ queryKey: ['artefacts', projectId] });
      } else {
        setRepairMsg(r.message ?? r.issues?.join('; ') ?? 'Could not repair the diagram.');
      }
    } catch (err) {
      setRepairMsg(err instanceof Error ? err.message : 'Repair failed');
    } finally {
      setRepairing(null);
    }
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const a = detail.data?.artefact;
  const ext = extOf(a?.storageKey);
  const fileName = a?.storageKey ? a.storageKey.slice(a.storageKey.lastIndexOf('/') + 1) : `${a?.type ?? 'artifact'}.txt`;
  const ctx: ViewerContext | null = a
    ? { content: a.content, ext, type: a.type, filename: fileName }
    : null;
  const plugin = ctx ? resolveViewer(ctx) : null;
  const diagramKind = diagramKindOf(plugin?.id);
  const repairable = plugin?.id === 'mermaid' || plugin?.id === 'plantuml' || plugin?.id === 'structurizr';
  const tabs = plugin?.hasSource ? [plugin.label, 'Source'] : [];
  const activeTab = tab || tabs[0] || '';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div
        className="flex max-h-[90vh] w-full max-w-4xl flex-col rounded-2xl bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 border-b border-slate-200 px-5 py-3">
          {a && (
            <>
              <span className="rounded bg-brand-50 px-2 py-0.5 text-[11px] font-bold text-brand-700">
                P{a.phase} · {a.type}
              </span>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-semibold text-slate-800">{a.title}</div>
                {a.storageKey && (
                  <div className="truncate font-mono text-[10px] text-slate-400" title={a.storageKey}>
                    📁 {a.storageKey} · {a.storageMode}
                  </div>
                )}
              </div>
              {canRepair && repairable && (
                <div className="flex shrink-0 items-center gap-1" title="Fix or regenerate this diagram if it won't render">
                  <button
                    onClick={() => repair('fix')}
                    disabled={repairing !== null}
                    className="rounded-lg border border-amber-300 bg-amber-50 px-2.5 py-1 text-xs font-semibold text-amber-800 hover:bg-amber-100 disabled:opacity-40"
                    title="Auto-fix syntax errors, preserving the diagram's content"
                  >
                    {repairing === 'fix' ? 'Fixing…' : '🔧 Fix syntax'}
                  </button>
                  <button
                    onClick={() => repair('regenerate')}
                    disabled={repairing !== null}
                    className="rounded-lg border border-slate-300 px-2.5 py-1 text-xs font-semibold text-slate-600 hover:border-brand-400 hover:text-brand-700 disabled:opacity-40"
                    title="Let the AI redraw this diagram from the same intent"
                  >
                    {repairing === 'regenerate' ? 'Regenerating…' : '♻ Regenerate'}
                  </button>
                </div>
              )}
              {a.canEdit && !editing && (
                <>
                  <button
                    onClick={() => { setDraft(a.content); setEditing(true); setSaveMsg(''); }}
                    className="rounded-lg border border-brand-300 bg-brand-50 px-2.5 py-1 text-xs font-semibold text-brand-700 hover:bg-brand-100"
                    title={diagramKind ? 'Open the diagram workbench (source + live preview)' : 'Edit this artifact and save in place'}
                  >
                    {diagramKind ? '✏️ Edit diagram' : '✏️ Edit'}
                  </button>
                  <label
                    className="cursor-pointer rounded-lg border border-slate-300 px-2.5 py-1 text-xs font-semibold text-slate-600 hover:border-brand-400 hover:text-brand-700"
                    title="Replace this artifact's content from a file (e.g. a .drawio edited in the desktop app)"
                  >
                    ⬆ Replace
                    <input
                      type="file"
                      className="hidden"
                      onChange={(e) => { const f = e.target.files?.[0]; if (f) void replaceFile(f); e.target.value = ''; }}
                    />
                  </label>
                </>
              )}
              <a
                href={`/api/projects/${projectId}/artefacts/${a.id}/download`}
                download={fileName}
                className="rounded-lg bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-600 hover:bg-brand-100 hover:text-brand-700"
                title="Download the file — opens with the application installed on your desktop"
              >
                ⬇ Open externally
              </a>
              {a.url && (
                <a href={a.url} target="_blank" rel="noreferrer" className="text-xs text-brand-600 underline">
                  Open in tool ↗
                </a>
              )}
            </>
          )}
          <button onClick={onClose} className="rounded px-2 py-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700">
            ✕
          </button>
        </div>

        {repairMsg && (
          <div
            className={`border-b px-5 py-1.5 text-xs ${
              repairMsg.startsWith('✓') ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-amber-200 bg-amber-50 text-amber-800'
            }`}
          >
            {repairMsg}
          </div>
        )}
        {saveMsg && (
          <div
            className={`border-b px-5 py-1.5 text-xs ${
              saveMsg.startsWith('✓') ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-amber-200 bg-amber-50 text-amber-800'
            }`}
          >
            {saveMsg}
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          {detail.isLoading && <div className="animate-pulse text-sm text-slate-400">Loading artifact…</div>}
          {detail.isError && (
            <div className="rounded bg-red-50 px-3 py-2 text-sm text-red-700">
              {detail.error instanceof Error ? detail.error.message : 'Failed to load artifact'}
            </div>
          )}
          {a && editing && diagramKind && (
            <DiagramWorkbench
              kind={diagramKind}
              source={a.content}
              saving={saving}
              onSave={(s) => void saveContent(s, diagramKind !== 'drawio')}
              onExit={() => { setEditing(false); setSaveMsg(''); }}
            />
          )}
          {a && editing && !diagramKind && (
            <div className="flex h-full min-h-[50vh] flex-col gap-2">
              <div className="flex items-center gap-2 text-xs text-slate-500">
                <span className="rounded bg-brand-50 px-2 py-0.5 font-semibold text-brand-700">Editing</span>
                <span>{plugin?.id === 'markdown' ? 'Live preview on the right.' : 'Changes render on save.'} Secrets/PII are masked automatically.</span>
                <span className="ml-auto flex gap-2">
                  <button
                    onClick={() => void saveContent(draft)}
                    disabled={saving || draft === a.content}
                    className="rounded-lg bg-emerald-600 px-3 py-1 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-40"
                  >
                    {saving ? 'Saving…' : '💾 Save'}
                  </button>
                  <button
                    onClick={() => { setEditing(false); setSaveMsg(''); }}
                    disabled={saving}
                    className="rounded-lg border border-slate-300 px-3 py-1 text-xs font-semibold text-slate-600 hover:bg-slate-100 disabled:opacity-40"
                  >
                    Cancel
                  </button>
                </span>
              </div>
              {plugin?.id === 'markdown' ? (
                <div className="grid min-h-0 flex-1 grid-cols-2 gap-3">
                  <textarea
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    spellCheck={false}
                    className="min-h-0 w-full resize-none rounded-lg border border-slate-300 bg-slate-50 p-3 font-mono text-xs leading-relaxed text-slate-800 focus:border-brand-500 focus:outline-none"
                  />
                  <div className="min-h-0 overflow-auto rounded-lg border border-slate-200 bg-white p-3">
                    <MarkdownDoc content={draft} />
                  </div>
                </div>
              ) : (
                <textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  spellCheck={false}
                  className="min-h-0 flex-1 w-full resize-none rounded-lg border border-slate-300 bg-slate-50 p-3 font-mono text-xs leading-relaxed text-slate-800 focus:border-brand-500 focus:outline-none"
                />
              )}
            </div>
          )}
          {a && ctx && plugin && !editing && (
            <>
              {tabs.length > 0 && <TabBar tabs={tabs} active={activeTab} onSelect={setTab} />}
              {activeTab === 'Source'
                ? <CodeView source={a.content} lang="plaintext" filename={fileName} showDiagnostics={false} />
                : plugin.render(ctx)}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
