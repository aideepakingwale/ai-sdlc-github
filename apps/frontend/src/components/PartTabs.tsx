import { useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { orderParts } from '../lib/partOrder';

export interface PartTab {
  field: string;
  title?: string;
  text: string;
  status: 'running' | 'done' | 'failed';
  error?: string | null;
}

const ICON: Record<PartTab['status'], string> = { running: '⏳', done: '✓', failed: '✗' };

/** True when the text is a JSON/code payload rather than prose markdown. */
const isStructured = (t: string) => /^\s*[{[]/.test(t);

/**
 * Every generated file of a stage as a tab, so parallel per-artifact generation is
 * visible side by side — with a live status per file. Files that finished before the
 * user navigated away / the app restarted come back from the server with their text.
 */
export function PartTabs({ parts, retrigger }: { parts: PartTab[]; retrigger?: (field: string) => void }) {
  // Active work first, failed next, finished last — so a regeneration of a few artifacts shows
  // the ones being written right now up front instead of buried behind the reused ones.
  const ordered = useMemo(() => orderParts(parts), [parts]);
  const [active, setActive] = useState<string>('');
  const manual = useRef(false);            // the user picked a tab themselves
  const anyRunning = ordered.some((p) => p.status === 'running');
  useEffect(() => { if (!anyRunning) manual.current = false; }, [anyRunning]);
  useEffect(() => {
    // Follow the first (active) tab until the user picks one; keep their pick if it still exists.
    if (!manual.current || !ordered.some((p) => p.field === active)) setActive(ordered[0]?.field ?? '');
  }, [ordered, active]);
  const cur = ordered.find((p) => p.field === active) ?? ordered[0];
  if (!cur) return null;
  return (
    <div className="rounded-lg border border-blue-200 bg-white" data-testid="part-tabs">
      <div role="tablist" className="flex gap-1 overflow-x-auto border-b border-blue-100 px-2 pt-1.5">
        {ordered.map((p) => (
          <button
            key={p.field} type="button" role="tab" aria-selected={p.field === cur.field}
            onClick={() => { manual.current = true; setActive(p.field); }}
            className={`shrink-0 rounded-t-md border border-b-0 px-2.5 py-1 text-[11px] font-semibold ${
              p.field === cur.field ? 'border-blue-200 bg-blue-50 text-blue-800' : 'border-transparent text-slate-500 hover:text-slate-700'
            }`}
          >
            <span className={p.status === 'failed' ? 'text-red-600' : p.status === 'done' ? 'text-emerald-600' : 'animate-pulse text-blue-500'}>
              {ICON[p.status]}
            </span>{' '}
            {p.title || p.field}
          </button>
        ))}
      </div>
      <div className="flex items-center justify-between px-3 py-1 text-[10px] text-slate-400">
        <span>{cur.status === 'running' ? 'writing…' : cur.status === 'done' ? 'complete' : 'failed'}</span>
        <span>{cur.text.length.toLocaleString()} chars</span>
      </div>
      <div className="prose-chat max-h-96 overflow-auto px-3 pb-2 text-[13px] text-slate-800">
        {cur.status === 'failed' && (
          <div className="mb-2 flex items-center gap-2 text-xs text-red-600">
            <span className="min-w-0 flex-1 truncate" title={cur.error ?? ''}>{cur.error ?? 'Generation failed'}</span>
            {retrigger && (
              <button type="button" onClick={() => retrigger(cur.field)}
                className="shrink-0 rounded border border-brand-300 bg-brand-50 px-2 py-0.5 font-semibold text-brand-700 hover:bg-brand-100">
                ↺ Retrigger
              </button>
            )}
          </div>
        )}
        {cur.text
          ? isStructured(cur.text)
            ? <pre className="whitespace-pre-wrap break-words font-mono text-[12px]">{cur.text}</pre>
            : <ReactMarkdown>{cur.text}</ReactMarkdown>
          : cur.status === 'running' && <span className="text-xs text-slate-400">Waiting for the first tokens…</span>}
        {cur.status === 'running' && <span className="inline-block h-3 w-1.5 animate-pulse bg-blue-500 align-middle" />}
      </div>
    </div>
  );
}
