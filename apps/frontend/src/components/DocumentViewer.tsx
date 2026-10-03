import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useQueries } from '@tanstack/react-query';
import { api } from '../api/client';
import { MarkdownDoc, resolveViewer } from './viewerRegistry';
import {
  composeMarkdown, fileSlug, humanize, orderForDocument, prepareMarkdown, sectionKind, sectionTitle, type DocItem,
} from '../lib/docModel';
import { copyRich, copyText, downloadBlob, exportDocx, printDocument } from '../lib/docExport';

/**
 * Read a stage's output as ONE document: narrative (HLD, LLD …) with its diagrams (Mermaid,
 * PlantUML, draw.io, C4) rendered in place, a contents list, and Copy / Word / PDF / Markdown
 * export of exactly what is on screen. Renders in a portal so printing can show only the document.
 */
export default function DocumentViewer({
  title, subtitle, items, onClose,
}: { title: string; subtitle?: string; items: DocItem[]; onClose: () => void }) {
  const ordered = useMemo(() => orderForDocument(items), [items]);
  const paperRef = useRef<HTMLDivElement>(null);
  const [busy, setBusy] = useState<'' | 'copy' | 'docx' | 'pdf'>('');
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [host, setHost] = useState<HTMLElement | null>(null);

  useEffect(() => {
    const el = document.createElement('div');
    el.id = 'doc-print-root';
    document.body.appendChild(el);
    setHost(el);
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => { window.removeEventListener('keydown', onKey); el.remove(); };
  }, [onClose]);

  const markdown = () => composeMarkdown(title, items);
  const run = async (kind: 'copy' | 'docx' | 'pdf' | 'md' | 'mdcopy', fn: () => Promise<string | void>) => {
    setMsg(null);
    if (kind === 'copy' || kind === 'docx' || kind === 'pdf') setBusy(kind);
    try {
      const text = await fn();
      setMsg({ ok: true, text: text || 'Done' });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : 'Export failed' });
    } finally {
      setBusy('');
    }
  };

  const doCopy = () => run('copy', async () => {
    const how = await copyRich(paperRef.current!, markdown());
    return how === 'rich' ? 'Copied — paste into Word, Google Docs or an email (diagrams included as images)'
      : 'Copied as Markdown text';
  });
  const doDocx = () => run('docx', async () => {
    const blob = await exportDocx(paperRef.current!, { title, subtitle });
    downloadBlob(blob, `${fileSlug(title)}.docx`);
    return 'Word document downloaded';
  });
  const doPdf = () => run('pdf', async () => {
    await printDocument(paperRef.current!, title);
    return 'In the print dialog choose “Save as PDF”';
  });
  const doMd = () => run('md', async () => {
    downloadBlob(new Blob([markdown()], { type: 'text/markdown' }), `${fileSlug(title)}.md`);
    return 'Markdown downloaded';
  });
  const doMdCopy = () => run('mdcopy', async () => { await copyText(markdown()); return 'Markdown copied'; });

  if (!host) return null;
  const btn = 'rounded-lg border border-slate-300 bg-white px-2.5 py-1 text-xs font-semibold text-slate-700 hover:border-brand-400 hover:text-brand-700 disabled:opacity-40';
  return createPortal(
    <div className="fixed inset-0 z-[60] flex flex-col bg-slate-100" role="dialog" aria-label={`${title} — document view`}>
      <div data-export-skip className="flex flex-wrap items-center gap-2 border-b border-slate-200 bg-white px-4 py-2 print:hidden">
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-slate-800">📄 {title}</div>
          <div className="text-[11px] text-slate-400">{ordered.length} section(s) · diagrams rendered in place</div>
        </div>
        <button className={btn} onClick={doCopy} disabled={Boolean(busy)} title="Copy formatted content (with diagram images) to paste into Word / Docs / email">
          {busy === 'copy' ? 'Copying…' : '⧉ Copy'}
        </button>
        <button className={btn} onClick={doDocx} disabled={Boolean(busy)} title="Download as a Word document (.docx)">
          {busy === 'docx' ? 'Building…' : '⬇ Word (.docx)'}
        </button>
        <button className={btn} onClick={doPdf} disabled={Boolean(busy)} title="Print to PDF">
          {busy === 'pdf' ? 'Preparing…' : '⬇ PDF'}
        </button>
        <button className={btn} onClick={doMd} disabled={Boolean(busy)} title="Download as Markdown (diagrams as fenced source)">⬇ .md</button>
        <button className={btn} onClick={doMdCopy} disabled={Boolean(busy)} title="Copy Markdown source">Copy .md</button>
        <button onClick={onClose} className="rounded px-2 py-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700" aria-label="Close document view">✕</button>
      </div>
      {msg && (
        <div data-export-skip role="status"
          className={`border-b px-4 py-1.5 text-xs print:hidden ${msg.ok ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-amber-200 bg-amber-50 text-amber-800'}`}>
          {msg.text}
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        <nav data-export-skip aria-label="Contents" className="hidden w-60 shrink-0 overflow-y-auto border-r border-slate-200 bg-white p-3 text-xs lg:block print:hidden">
          <div className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-slate-400">Contents</div>
          <ol className="space-y-1">
            {ordered.map((it, i) => (
              <li key={it.id}>
                <a href={`#doc-sec-${i}`} className="block truncate rounded px-2 py-1 text-slate-600 hover:bg-slate-100 hover:text-brand-700"
                  onClick={(e) => { e.preventDefault(); document.getElementById(`doc-sec-${i}`)?.scrollIntoView({ block: 'start' }); }}>
                  {i + 1}. {sectionTitle(it)}
                </a>
              </li>
            ))}
          </ol>
        </nav>

        <div className="min-w-0 flex-1 overflow-y-auto p-4 print:overflow-visible print:p-0">
          <article ref={paperRef} className="doc-paper mx-auto max-w-4xl rounded-lg bg-white p-8 shadow-sm print:shadow-none">
            <h1 data-docx-skip className="mb-1 text-3xl font-bold text-navy">{title}</h1>
            <p data-docx-skip className="mb-6 text-xs text-slate-400">
              {subtitle ? `${subtitle} · ` : ''}Generated {new Date().toLocaleDateString()}
            </p>
            {ordered.map((it, i) => {
              const kind = sectionKind(it);
              const ext = it.ext ?? '';
              return (
                <section key={it.id} id={`doc-sec-${i}`} className="mb-8">
                  <h2 className="mb-3 border-b border-slate-200 pb-1 text-xl font-semibold text-navy">
                    {i + 1}. {sectionTitle(it)}
                  </h2>
                  {kind === 'markdown' && <MarkdownDoc content={prepareMarkdown(it.content)} />}
                  {kind === 'diagram' && resolveViewer({ content: it.content, ext, type: it.type, filename: `${it.type}${ext}` })
                    .render({ content: it.content, ext, type: it.type, filename: `${it.type}${ext}` })}
                  {kind === 'code' && (
                    <>
                      <p className="mb-1 text-[11px] uppercase tracking-wide text-slate-400">{humanize(it.type)}</p>
                      <pre className="overflow-auto whitespace-pre-wrap break-words rounded border border-slate-200 bg-slate-50 p-3 font-mono text-[11px] leading-snug">
                        <code>{it.content}</code>
                      </pre>
                    </>
                  )}
                </section>
              );
            })}
          </article>
        </div>
      </div>
    </div>,
    host,
  );
}

interface ArtefactRef { id: string; type: string; title: string; phase: number }

/** Loads every artifact of a stage, then shows them as one document. */
export function StageDocument({
  projectId, artefacts, title, subtitle, onClose,
}: { projectId: string; artefacts: ArtefactRef[]; title: string; subtitle?: string; onClose: () => void }) {
  const results = useQueries({
    queries: artefacts.map((a) => ({
      queryKey: ['artefact', projectId, a.id],
      queryFn: () => api.get<{ artefact: { id: string; type: string; title: string; content: string; storageKey?: string | null } }>(
        `/api/projects/${projectId}/artefacts/${a.id}`),
    })),
  });
  const loading = results.some((r) => r.isLoading);
  const items: DocItem[] = results.flatMap((r) => {
    const a = r.data?.artefact;
    if (!a) return [];
    const dot = a.storageKey ? a.storageKey.lastIndexOf('.') : -1;
    return [{ id: a.id, type: a.type, title: a.title, content: a.content ?? '', ext: dot > 0 ? a.storageKey!.slice(dot).toLowerCase() : '' }];
  });
  if (loading) {
    return createPortal(
      <div className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-900/30" onClick={onClose}>
        <div className="animate-pulse rounded-lg bg-white px-6 py-4 text-sm text-slate-500">Loading {artefacts.length} artifact(s)…</div>
      </div>, document.body);
  }
  return <DocumentViewer title={title} subtitle={subtitle} items={items} onClose={onClose} />;
}
