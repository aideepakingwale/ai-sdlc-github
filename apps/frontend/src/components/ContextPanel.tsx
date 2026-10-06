import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';
import { Badge } from './ui/Badge';
import { Button } from './ui/Button';
import { Icon } from './ui/Icon';
import ContextGraphView from './ContextGraphView';
import ProjectContextGraph from './ProjectContextGraph';
import { LAYER_COLORS, STATUS_LABEL, coverage, formatChars, type ContextManifest, type ContextView, type ManifestItem } from '../lib/contextGraph';

/**
 * "What this stage knows": a collapsible, read-only summary of the context the stage is (or was) given —
 * fixed instructions, project profile, standards & templates, previous stages, your instructions,
 * attached material and retrieved knowledge — with a link to the full interactive view. Shown to anyone
 * who can read the project. `refreshKey` changes when the plan or a run changes what the stage knows.
 */
export default function ContextPanel({ projectId, seq, refreshKey }: { projectId: string; seq: number; refreshKey: string }) {
  const [open, setOpen] = useState(false);
  const [full, setFull] = useState(false);
  const [project, setProject] = useState(false);
  const [mode, setMode] = useState<'preview' | 'actual'>('preview');
  const q = useQuery({
    queryKey: ['context', projectId, seq, refreshKey],
    queryFn: () => api.get<ContextView>(`/api/projects/${projectId}/phase/${seq}/context`),
    enabled: Boolean(projectId),
    staleTime: 0,
  });
  const view = q.data;
  const manifest: ContextManifest | null = view ? (mode === 'actual' && view.actual ? view.actual : view.preview) : null;

  return (
    <section className="mt-5 rounded-xl border border-slate-200 bg-white" data-testid="context-panel">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
        className="flex w-full flex-wrap items-center gap-3 px-4 py-3 text-left">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-slate-100 text-slate-600"><Icon name="layers" size={14} /></span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-semibold text-slate-800">What this stage knows</span>
          <span className="block text-xs text-slate-500">
            {manifest
              ? `${manifest.totals.items} items · ≈${formatChars(manifest.totals.tokens)} tokens${manifest.totals.condensed ? ` · ${manifest.totals.condensed} condensed` : ''}`
              : q.isError ? 'Could not load the context' : 'Loading…'}
          </span>
        </span>
        {manifest && <StackedBar manifest={manifest} />}
        <Icon name={open ? 'chevron-down' : 'chevron-right'} size={16} className="text-slate-400" />
      </button>

      {open && manifest && view && (
        <div className="border-t border-slate-100 px-4 py-3">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <div className="inline-flex overflow-hidden rounded-lg border border-slate-300 text-xs font-semibold" role="group" aria-label="Which context">
              <button type="button" onClick={() => setMode('preview')} aria-pressed={mode === 'preview'}
                className={`px-2.5 py-1 ${mode === 'preview' ? 'bg-brand-600 text-white' : 'bg-white text-slate-600'}`}>Will be sent</button>
              <button type="button" onClick={() => view.actual && setMode('actual')} aria-pressed={mode === 'actual'} disabled={!view.actual}
                className={`px-2.5 py-1 ${mode === 'actual' ? 'bg-brand-600 text-white' : 'bg-white text-slate-600'} disabled:opacity-40`}
                title={view.actual ? undefined : 'This stage has not run yet'}>Was sent</button>
            </div>
            {mode === 'actual' && view.actual?.ranAt && <span className="text-[11px] text-slate-400">last run {new Date(view.actual.ranAt).toLocaleString()}</span>}
            <span className="flex-1" />
            <Button size="sm" variant="ghost" icon="layers" onClick={() => setProject(true)} title="How context flows through the whole project">Whole project</Button>
            <Button size="sm" variant="secondary" icon="search" onClick={() => setFull(true)}>Open full view</Button>
          </div>
          <div className="space-y-3">
            {manifest.layers.map((l) => (
              <div key={l.id}>
                <div className="flex items-center gap-2 text-xs">
                  <span className="h-2 w-2 rounded-full" style={{ background: LAYER_COLORS[l.id] }} />
                  <span className="font-semibold text-slate-700">{l.label}</span>
                  <span className="text-slate-400">{l.items.length} · ≈{formatChars(l.tokens)} tok</span>
                </div>
                <p className="mb-1 ml-4 text-[11px] text-slate-400">{l.hint}</p>
                <ul className="ml-4 space-y-0.5">
                  {l.items.map((i) => <ItemRow key={i.id} item={i} />)}
                </ul>
              </div>
            ))}
          </div>
        </div>
      )}
      {project && <ProjectContextGraph projectId={projectId} onClose={() => setProject(false)} />}
      {full && view && <ContextGraphView view={view} initialMode={mode} onClose={() => setFull(false)} />}
    </section>
  );
}

function ItemRow({ item }: { item: ManifestItem }) {
  const tone = item.status === 'full' ? 'success' : item.status === 'excluded' ? 'neutral' : 'warning';
  return (
    <li className="flex flex-wrap items-center gap-2 text-xs">
      <span className={`min-w-0 flex-1 truncate ${item.status === 'excluded' ? 'text-slate-400 line-through' : 'text-slate-700'}`} title={item.label}>{item.label}</span>
      <span className="text-[11px] text-slate-400" title={coverage(item)}>{item.status === 'excluded' ? '' : `${formatChars(item.chars)}${item.totalChars > item.chars ? ` / ${formatChars(item.totalChars)}` : ''}`}</span>
      <Badge tone={tone} title={item.note || coverage(item)}>{STATUS_LABEL[item.status]}</Badge>
    </li>
  );
}

function StackedBar({ manifest }: { manifest: ContextManifest }) {
  const total = Math.max(1, manifest.totals.chars);
  return (
    <span className="hidden h-2 w-40 overflow-hidden rounded-full bg-slate-100 sm:flex" aria-label="Share of the context by layer" role="img">
      {manifest.layers.filter((l) => l.chars > 0).map((l) => (
        <span key={l.id} title={`${l.label}: ≈${formatChars(l.tokens)} tokens`} style={{ width: `${(l.chars / total) * 100}%`, background: LAYER_COLORS[l.id] }} />
      ))}
    </span>
  );
}
