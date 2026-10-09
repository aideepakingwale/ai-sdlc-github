import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api, streamCodeEdit } from '../api/client';
import MiddleText from '../v2/MiddleText';
import DiffView from './DiffView';
import {
  ACTION_LABEL, applyEvent, effectiveTargets, emptySession, fromSaved, startTurn,
  type EditEvent, type EditScope, type EditSession, type SavedSession, type ToolStep,
} from '../lib/codeEdit';

interface Props {
  projectId: string;
  scope: EditScope;
  /** Files or folders ticked in the explorer. */
  checked: string[];
  onUncheck: (path: string) => void;
  onClearChecked: () => void;
  /** The file open in the explorer: the target when nothing is ticked. */
  openFile: string | null;
  onOpenFile?: (path: string) => void;
  canEdit: boolean;
  blockedReason?: string;
  /** Called after changes are applied or undone so the explorer reloads its files. */
  onFilesChanged: () => void;
}

const TOOL_ICON: Record<string, string> = { list_dir: '📁', read_file: '📖', search: '🔎', edit_file: '✏️', write_file: '📝', delete_file: '🗑️', check_syntax: '✅' };

/**
 * The code assistant: pick files or folders in the explorer, say what to change, and watch it work (read, search, edit, check).
 * What it proposes arrives as a diff per file. Nothing is written until a person applies the files they accept; applied changes can be undone.
 */
export default function CodeAssistant({ projectId, scope, checked, onUncheck, onClearChecked, openFile, onOpenFile, canEdit, blockedReason, onFilesChanged }: Props) {
  const qc = useQueryClient();
  const [session, setSession] = useState<EditSession>(emptySession());
  const [prompt, setPrompt] = useState('');
  const [whole, setWhole] = useState(false);
  const [busy, setBusy] = useState(false);
  const [accept, setAccept] = useState<Set<string>>(new Set());
  const [openDiff, setOpenDiff] = useState<Set<string>>(new Set());
  const [note, setNote] = useState('');
  const abort = useRef<AbortController | null>(null);
  const feed = useRef<HTMLDivElement>(null);
  const seen = useRef<Set<string>>(new Set());
  const targets = effectiveTargets(checked, openFile, whole);
  const needsTarget = !whole && targets.length === 0;

  const history = useQuery({
    queryKey: ['code-edit-list', projectId, scope],
    queryFn: () => api.get<{ sessions: Array<{ id: string; title: string; status: string; pending: number; applied: number; turns: number }> }>(`/api/projects/${projectId}/code-edit?scope=${scope}`),
  });

  // Every file the assistant proposes starts ticked; a new proposal re-ticks only what is new.
  const proposed = session.changes.map((c) => c.path).join('\n');
  useEffect(() => {
    const paths = session.changes.map((c) => c.path);
    if (paths.length === 0) seen.current.clear();
    const fresh = paths.filter((p) => !seen.current.has(p));
    fresh.forEach((p) => seen.current.add(p));
    setAccept((cur) => new Set(paths.filter((p) => cur.has(p) || fresh.includes(p))));
  }, [proposed]);
  useEffect(() => { feed.current?.scrollTo({ top: feed.current.scrollHeight }); }, [session.turns]);

  async function send() {
    const text = prompt.trim();
    if (!text || busy || needsTarget) return;
    setPrompt(''); setNote(''); setBusy(true);
    setSession((s) => startTurn(s, text));
    const ctl = new AbortController();
    abort.current = ctl;
    try {
      await streamCodeEdit<EditEvent>(projectId, { scope, prompt: text, targets, sessionId: session.id }, (e) => setSession((s) => applyEvent(s, e)), ctl.signal);
    } catch (err) {
      if ((err as Error).name !== 'AbortError') setSession((s) => applyEvent(s, { type: 'error', code: 'NETWORK', message: 'The connection was lost. What was proposed so far is kept.' }));
    } finally {
      abort.current = null; setBusy(false);
      void qc.invalidateQueries({ queryKey: ['code-edit-list', projectId, scope] });
    }
  }

  async function reload(id: string) {
    const r = await api.get<SavedSession>(`/api/projects/${projectId}/code-edit/${id}`);
    setSession(fromSaved(r));
    void qc.invalidateQueries({ queryKey: ['code-edit-list', projectId, scope] });
    return r;
  }

  const apply = useMutation({
    mutationFn: () => api.post<{ applied: string[]; remaining: string[] }>(`/api/projects/${projectId}/code-edit/${session.id}/apply`, { paths: [...accept] }),
    onSuccess: async (r) => { setNote(`Applied ${r.applied.length} file${r.applied.length === 1 ? '' : 's'}.`); await reload(session.id!); onFilesChanged(); },
    onError: (e) => setNote(e instanceof Error ? e.message : 'Could not apply the changes'),
  });
  const revert = useMutation({
    mutationFn: () => api.post(`/api/projects/${projectId}/code-edit/${session.id}/revert`),
    onSuccess: async () => { setNote('Undone. The files are back as they were.'); await reload(session.id!); onFilesChanged(); },
    onError: (e) => setNote(e instanceof Error ? e.message : 'Could not undo'),
  });
  const discard = useMutation({
    mutationFn: () => api.post(`/api/projects/${projectId}/code-edit/${session.id}/discard`),
    onSuccess: async () => { setNote('Proposal discarded.'); await reload(session.id!); },
  });

  const changes = session.changes;
  const total = useMemo(() => changes.reduce((a, c) => ({ add: a.add + c.added, del: a.del + c.removed }), { add: 0, del: 0 }), [changes]);
  const idle = !busy && !apply.isPending && !revert.isPending && !discard.isPending;
  const reviewing = changes.length > 0 && !busy;
  const toggle = (set: Set<string>, p: string) => { const n = new Set(set); if (n.has(p)) n.delete(p); else n.add(p); return n; };

  return (
    <section className="mt-4 rounded-xl border border-slate-300 bg-white" data-testid="code-assistant" aria-label="Code assistant">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 px-3 py-2">
        <span className="text-sm font-semibold text-navy">Code assistant</span>
        <span className="text-xs text-slate-500">Edits {scope === 'generated' ? 'the generated code' : 'the uploaded codebase'}. You review a diff; nothing changes until you apply it.</span>
        <span className="ml-auto flex items-center gap-2">
          {(history.data?.sessions.length ?? 0) > 0 && (
            <select aria-label="Earlier requests" value={session.id ?? ''} disabled={busy} data-testid="assistant-history"
              onChange={(e) => { const id = e.target.value; if (!id) { setSession(emptySession()); setNote(''); } else void reload(id); }}
              className="max-w-[16rem] rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs">
              <option value="">New request</option>
              {history.data!.sessions.map((s) => <option key={s.id} value={s.id}>{s.title} · {s.status}</option>)}
            </select>
          )}
          {session.id && <button type="button" disabled={busy} onClick={() => { setSession(emptySession()); setNote(''); }} className="rounded-lg border border-slate-300 px-2 py-1 text-xs font-semibold text-slate-700 hover:border-brand-400 disabled:opacity-50">New request</button>}
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-1.5 border-b border-slate-100 px-3 py-2" data-testid="assistant-targets">
        <span className="text-xs font-semibold text-slate-600">Works on</span>
        {whole ? <span className="rounded-full bg-brand-100 px-2 py-0.5 text-xs font-semibold text-brand-700">the whole codebase</span>
          : checked.length > 0 ? checked.map((p) => (
            <span key={p} className="inline-flex max-w-[18rem] items-center gap-1 rounded-full bg-brand-100 px-2 py-0.5 text-xs text-brand-700" title={p}>
              <MiddleText text={p} className="min-w-0 font-mono" />
              <button type="button" onClick={() => onUncheck(p)} aria-label={`Remove ${p}`} className="shrink-0 font-bold hover:text-bared-600">×</button>
            </span>
          )) : openFile ? <span className="inline-flex max-w-[18rem] items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-700" title={openFile}><MiddleText text={openFile} className="min-w-0 font-mono" /><span className="shrink-0 text-slate-400">(open file)</span></span>
            : <span className="text-xs text-slate-500">Tick files or folders in the tree, or open a file.</span>}
        <span className="ml-auto flex items-center gap-2 text-xs">
          {checked.length > 0 && !whole && <button type="button" onClick={onClearChecked} className="text-slate-500 hover:text-navy">Clear</button>}
          <label className="inline-flex items-center gap-1 text-slate-600"><input type="checkbox" checked={whole} onChange={(e) => setWhole(e.target.checked)} data-testid="assistant-whole" />Whole codebase</label>
        </span>
      </div>

      <div ref={feed} className="max-h-[26rem] space-y-3 overflow-auto px-3 py-3" data-testid="assistant-feed" aria-live="polite">
        {session.turns.length === 0 && (
          <div className="rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
            Describe a change in plain words, for example <i>“make total() ignore None values and add a unit test”</i>. The assistant reads the code, searches for callers, edits and checks its work, then shows you every change as a diff.
          </div>
        )}
        {session.turns.map((t, i) => (
          <div key={i} className="space-y-1.5">
            <div className="ml-auto max-w-[85%] rounded-lg bg-brand-600 px-3 py-1.5 text-sm text-white" data-testid="assistant-prompt">{t.prompt}</div>
            {t.items.map((it, j) => it.kind === 'message'
              ? <p key={j} className="text-sm text-slate-700">{it.text}</p>
              : <StepRow key={it.id + j} step={it} />)}
            {t.running && <div className="flex items-center gap-2 text-xs text-slate-500"><span className="h-2 w-2 animate-pulse rounded-full bg-brand-500" />Working…</div>}
            {t.error && <div className="rounded-lg border border-bared-500/40 bg-red-50 px-3 py-2 text-xs text-red-800" role="alert">{t.error}</div>}
            {t.summary && !t.running && <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-800" data-testid="assistant-summary">{t.summary}</div>}
          </div>
        ))}
      </div>

      {(changes.length > 0 || session.applied.length > 0 || note) && (
        <div className="border-t border-slate-200 px-3 py-3" data-testid="assistant-changes">
          {changes.length > 0 && (
            <>
              <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
                <span className="font-semibold text-slate-700">{changes.length} file{changes.length === 1 ? '' : 's'} changed</span>
                <span className="text-emerald-700">+{total.add}</span><span className="text-red-700">−{total.del}</span>
                {session.model && <span className="text-slate-400">· {session.model}{session.tokens ? ` · ${session.tokens.toLocaleString()} tokens` : ''}</span>}
              </div>
              <ul className="space-y-1.5">
                {changes.map((c) => (
                  <li key={c.path} className="rounded-lg border border-slate-200">
                    <div className="flex items-center gap-2 px-2 py-1.5 text-xs">
                      <input type="checkbox" checked={accept.has(c.path)} disabled={!reviewing} onChange={() => setAccept((a) => toggle(a, c.path))} aria-label={`Apply ${c.path}`} />
                      <button type="button" onClick={() => setOpenDiff((o) => toggle(o, c.path))} aria-expanded={openDiff.has(c.path)} className="flex min-w-0 flex-1 items-center gap-2 text-left">
                        <span className="text-slate-400">{openDiff.has(c.path) ? '▾' : '▸'}</span>
                        <MiddleText text={c.path} className="min-w-0 flex-1 font-mono text-slate-800" />
                      </button>
                      <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold ${c.action === 'create' ? 'bg-emerald-100 text-emerald-800' : c.action === 'delete' ? 'bg-red-100 text-red-800' : 'bg-slate-100 text-slate-700'}`}>{ACTION_LABEL[c.action]}</span>
                      <span className="shrink-0 font-mono text-emerald-700">+{c.added}</span><span className="shrink-0 font-mono text-red-700">−{c.removed}</span>
                      {onOpenFile && c.action !== 'create' && <button type="button" onClick={() => onOpenFile(c.path)} className="shrink-0 text-slate-400 hover:text-brand-700" title="Open the file as it is now">Open</button>}
                    </div>
                    {openDiff.has(c.path) && <div className="px-2 pb-2"><DiffView diff={c.diff} /></div>}
                  </li>
                ))}
              </ul>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <button type="button" disabled={!reviewing || !idle || accept.size === 0 || !canEdit} onClick={() => apply.mutate()} data-testid="assistant-apply"
                  className="rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-50">
                  {apply.isPending ? 'Applying…' : accept.size === changes.length ? (changes.length === 1 ? 'Apply change' : 'Apply all') : `Apply ${accept.size} of ${changes.length}`}
                </button>
                <button type="button" disabled={!reviewing || !idle} onClick={() => discard.mutate()} data-testid="assistant-discard"
                  className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:border-bared-500 hover:text-bared-600 disabled:opacity-50">Discard</button>
                <button type="button" onClick={() => setOpenDiff(openDiff.size ? new Set() : new Set(changes.map((c) => c.path)))} className="ml-auto text-xs text-slate-500 hover:text-navy">{openDiff.size ? 'Collapse diffs' : 'Expand diffs'}</button>
              </div>
            </>
          )}
          {session.applied.length > 0 && (
            <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-900" data-testid="assistant-applied">
              <span>Applied: {session.applied.map((c) => c.path.split('/').pop()).join(', ')}</span>
              <button type="button" disabled={!idle} onClick={() => revert.mutate()} data-testid="assistant-undo" className="ml-auto rounded-lg border border-emerald-300 bg-white px-2.5 py-1 font-semibold text-emerald-800 hover:bg-emerald-100 disabled:opacity-50">{revert.isPending ? 'Undoing…' : 'Undo'}</button>
            </div>
          )}
          {note && <div className="mt-2 text-xs text-slate-600" role="status">{note}</div>}
        </div>
      )}

      <div className="border-t border-slate-200 px-3 py-2">
        {!canEdit && <div className="mb-1 text-xs text-amber-700">{blockedReason || 'You do not have write access to the implementation stage, so you can review but not run the assistant.'}</div>}
        {blockedReason && canEdit && <div className="mb-1 text-xs text-amber-700">{blockedReason}</div>}
        <div className="flex items-end gap-2">
          <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={2} disabled={!canEdit || busy} aria-label="Describe the change" data-testid="assistant-input"
            onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void send(); } }}
            placeholder={session.id ? 'Ask for a follow-up (“also add a test”, “use a set instead”)…' : 'Describe the change you want…'}
            className="min-h-[2.75rem] flex-1 resize-y rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none disabled:bg-slate-50" />
          {busy
            ? <button type="button" onClick={() => abort.current?.abort()} data-testid="assistant-stop" className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-700 hover:border-bared-500 hover:text-bared-600">Stop</button>
            : <button type="button" onClick={() => void send()} disabled={!canEdit || !prompt.trim() || needsTarget} data-testid="assistant-send" className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-50">Send</button>}
        </div>
        <div className="mt-1 text-[11px] text-slate-400">Ctrl/⌘ + Enter to send · it can read and search the whole codebase but only changes what you selected · it cannot run tests, so check the result.</div>
      </div>
    </section>
  );
}

function StepRow({ step }: { step: ToolStep }) {
  const tone = step.status === 'error' ? 'text-red-700' : step.status === 'running' ? 'text-slate-500' : 'text-slate-600';
  return (
    <div className={`flex items-center gap-2 pl-2 text-xs ${tone}`} data-testid="assistant-step" title={step.detail || step.label}>
      <span aria-hidden="true">{TOOL_ICON[step.tool] ?? '•'}</span>
      <span className="min-w-0 flex-1 truncate">{step.label}</span>
      {step.status === 'running' && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand-500" />}
      {step.status === 'done' && <span className="text-emerald-600">✓</span>}
      {step.status === 'error' && <span>✕ {step.detail}</span>}
    </div>
  );
}
