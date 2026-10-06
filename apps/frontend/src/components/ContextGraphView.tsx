import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  LAYER_COLORS, STATUS_LABEL, coverage, formatChars, layoutGraph, neighbours, sourceInfo,
  type ContextManifest, type ContextView, type GEdge, type GNode,
} from '../lib/contextGraph';

/**
 * Full view of what a stage knows. READ-ONLY: you can zoom, pan, search, filter by layer and select a
 * node or a relationship to see where it came from and how much of it the stage received, but nothing
 * here changes what a stage is given. Pure SVG; no requests of its own (the data comes from the panel).
 */
export default function ContextGraphView({ view, initialMode, onClose }: { view: ContextView; initialMode: 'preview' | 'actual'; onClose: () => void }) {
  const [mode, setMode] = useState<'preview' | 'actual'>(initialMode === 'actual' && view.actual ? 'actual' : 'preview');
  const manifest: ContextManifest = mode === 'actual' && view.actual ? view.actual : view.preview;
  const [query, setQuery] = useState('');
  const [layers, setLayers] = useState<string[] | null>(null);          // null = all
  const [hideExcluded, setHideExcluded] = useState(false);
  const [selected, setSelected] = useState<{ type: 'node' | 'edge'; id: string } | null>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; px: number; py: number } | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const graph = useMemo(() => layoutGraph(manifest, { layers: layers ?? undefined, query, hideExcluded }), [manifest, layers, query, hideExcluded]);
  const nodeById = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n])), [graph]);
  const focus = useMemo(() => {
    if (!selected) return null;
    if (selected.type === 'node') return neighbours(graph, selected.id);
    const e = graph.edges.find((x) => x.id === selected.id);
    return e ? { nodes: new Set([e.from, e.to]), edges: new Set([e.id]) } : null;
  }, [graph, selected]);

  const selNode = selected?.type === 'node' ? nodeById.get(selected.id) : undefined;
  const selEdge = selected?.type === 'edge' ? graph.edges.find((e) => e.id === selected.id) : undefined;
  const clamp = (z: number) => Math.min(4, Math.max(0.3, z));
  const half = graph.width / 2;

  const toggleLayer = (id: string) => setLayers((cur) => {
    const all = manifest.layers.map((l) => l.id);
    const on = cur ?? all;
    const next = on.includes(id) ? on.filter((x) => x !== id) : [...on, id];
    return next.length === all.length ? null : next;
  });

  return createPortal(
    <div className="fixed inset-0 z-[60] flex flex-col bg-slate-100" role="dialog" aria-label={`What ${manifest.stage} knows — full view`}>
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 bg-white px-4 py-2">
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-slate-800">What “{manifest.stage}” knows</div>
          <div className="text-[11px] text-slate-400">
            {manifest.totals.items} items · ≈{formatChars(manifest.totals.tokens)} tokens (estimate) · {manifest.totals.condensed} condensed · {manifest.totals.excluded} not included · read-only
          </div>
        </div>
        <div className="inline-flex overflow-hidden rounded-lg border border-slate-300 text-xs font-semibold" role="group" aria-label="Which context">
          <button type="button" onClick={() => setMode('preview')} aria-pressed={mode === 'preview'}
            className={`px-2.5 py-1 ${mode === 'preview' ? 'bg-brand-600 text-white' : 'bg-white text-slate-600'}`}
            title="Built now from the saved plan: what would be sent">Will be sent</button>
          <button type="button" onClick={() => view.actual && setMode('actual')} aria-pressed={mode === 'actual'} disabled={!view.actual}
            className={`px-2.5 py-1 ${mode === 'actual' ? 'bg-brand-600 text-white' : 'bg-white text-slate-600'} disabled:opacity-40`}
            title={view.actual ? `What the last run was given (${new Date(view.actual.ranAt ?? '').toLocaleString()})` : 'This stage has not run yet'}>Was sent</button>
        </div>
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search the context…" aria-label="Search the context"
          className="w-48 rounded-lg border border-slate-300 px-2 py-1 text-xs focus:border-brand-400 focus:outline-none" />
        <label className="flex items-center gap-1 text-xs text-slate-600">
          <input type="checkbox" checked={hideExcluded} onChange={(e) => setHideExcluded(e.target.checked)} /> Hide not included
        </label>
        <button onClick={onClose} className="rounded px-2 py-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700" aria-label="Close full view">✕</button>
      </div>

      <div className="flex flex-wrap items-center gap-1.5 border-b border-slate-200 bg-white px-4 py-1.5" aria-label="Layers">
        {manifest.layers.map((l) => {
          const on = !layers || layers.includes(l.id);
          return (
            <button key={l.id} type="button" onClick={() => toggleLayer(l.id)} aria-pressed={on} title={l.hint}
              className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-semibold ${on ? 'border-slate-300 bg-white text-slate-700' : 'border-slate-200 bg-slate-50 text-slate-400'}`}>
              <span className="h-2 w-2 rounded-full" style={{ background: on ? LAYER_COLORS[l.id] : '#cbd5e1' }} />
              {l.label} <span className="font-normal text-slate-400">{l.items.length}</span>
            </button>
          );
        })}
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="relative min-w-0 flex-1 overflow-hidden bg-white">
          <svg
            data-testid="context-graph" className="h-full w-full cursor-grab touch-none select-none active:cursor-grabbing"
            viewBox={`${-half} ${-half} ${graph.width} ${graph.height}`} preserveAspectRatio="xMidYMid meet"
            onWheel={(e) => { e.preventDefault(); setZoom((z) => clamp(z * (e.deltaY < 0 ? 1.12 : 1 / 1.12))); }}
            onPointerDown={(e) => { drag.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y }; (e.currentTarget as SVGSVGElement).setPointerCapture(e.pointerId); }}
            onPointerMove={(e) => {
              const d = drag.current; if (!d) return;
              const scale = graph.width / (e.currentTarget.clientWidth || 1) / zoom;
              setPan({ x: d.px + (e.clientX - d.x) * scale, y: d.py + (e.clientY - d.y) * scale });
            }}
            onPointerUp={() => { drag.current = null; }}
            onClick={(e) => { if (e.target === e.currentTarget) setSelected(null); }}
          >
            <g transform={`scale(${zoom}) translate(${pan.x} ${pan.y})`}>
              {graph.edges.map((e) => <Edge key={e.id} e={e} dim={Boolean(focus) && !focus!.edges.has(e.id)} active={selected?.id === e.id} onSelect={() => setSelected({ type: 'edge', id: e.id })} />)}
              {graph.nodes.map((n) => (
                <Node key={n.id} n={n} dim={Boolean(focus) && !focus!.nodes.has(n.id)} active={selected?.id === n.id}
                  onSelect={() => setSelected({ type: 'node', id: n.id })} />
              ))}
            </g>
          </svg>
          <div className="absolute bottom-3 left-3 flex flex-col overflow-hidden rounded-lg border border-slate-300 bg-white text-sm shadow">
            <button className="px-2.5 py-1 hover:bg-slate-100" aria-label="Zoom in" onClick={() => setZoom((z) => clamp(z * 1.25))}>+</button>
            <button className="border-y border-slate-200 px-2.5 py-1 hover:bg-slate-100" aria-label="Zoom out" onClick={() => setZoom((z) => clamp(z / 1.25))}>−</button>
            <button className="px-2.5 py-1 text-[11px] hover:bg-slate-100" aria-label="Reset view" onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}>Fit</button>
          </div>
          <Legend />
          {graph.nodes.length <= 1 && <div className="absolute inset-0 flex items-center justify-center text-sm text-slate-400">Nothing matches the current filters.</div>}
        </div>

        <aside className="w-80 shrink-0 overflow-y-auto border-l border-slate-200 bg-white p-4 text-xs" aria-label="Details">
          {selNode?.item && <ItemDetail node={selNode} edges={graph.edges} nodeById={nodeById} onPick={(id) => setSelected({ type: 'node', id })} />}
          {selNode && !selNode.item && <PromptDetail node={selNode} manifest={manifest} />}
          {selEdge && <EdgeDetail edge={selEdge} nodeById={nodeById} onPick={(id) => setSelected({ type: 'node', id })} />}
          {!selected && <Overview manifest={manifest} diff={mode === 'actual' ? view.diff : null} />}
        </aside>
      </div>
    </div>,
    document.body,
  );
}

function Node({ n, dim, active, onSelect }: { n: GNode; dim: boolean; active: boolean; onSelect: () => void }) {
  const fill = n.kind === 'prompt' ? '#0b2a4a' : n.kind === 'output' ? '#fff' : LAYER_COLORS[n.layer ?? ''] ?? '#64748b';
  const excluded = n.status === 'excluded';
  return (
    <g role="button" tabIndex={0} aria-label={n.label} data-node={n.id} opacity={dim ? 0.18 : 1} className="cursor-pointer outline-none focus:outline-none"
      onClick={(e) => { e.stopPropagation(); onSelect(); }} onKeyDown={(e) => { if (e.key === 'Enter') onSelect(); }}
      onPointerDown={(e) => e.stopPropagation()}>
      <circle cx={n.x} cy={n.y} r={n.r} fill={excluded ? '#fff' : fill} fillOpacity={n.status === 'condensed' || n.status === 'summarised' ? 0.55 : 1}
        stroke={n.kind === 'output' ? '#0b2a4a' : fill} strokeWidth={active ? 4 : excluded || n.kind === 'output' ? 2 : 1}
        strokeDasharray={excluded ? '4 3' : undefined} />
      {n.kind === 'prompt' && <text x={n.x} y={n.y + 4} textAnchor="middle" fontSize={11} fill="#fff" fontWeight={600}>Prompt</text>}
      <text x={n.x} y={n.y + n.r + 13} textAnchor="middle" fontSize={11} fill="#334155" className="pointer-events-none">
        {n.label.length > 34 ? `${n.label.slice(0, 33)}…` : n.label}
      </text>
    </g>
  );
}

function Edge({ e, dim, active, onSelect }: { e: GEdge; dim: boolean; active: boolean; onSelect: () => void }) {
  const mx = (e.x1 + e.x2) / 2; const my = (e.y1 + e.y2) / 2;
  return (
    <g opacity={dim ? 0.1 : 1} data-edge={e.id} className="cursor-pointer" onClick={(ev) => { ev.stopPropagation(); onSelect(); }} onPointerDown={(ev) => ev.stopPropagation()}>
      <line x1={e.x1} y1={e.y1} x2={e.x2} y2={e.y2} stroke="transparent" strokeWidth={12} />
      <line x1={e.x1} y1={e.y1} x2={e.x2} y2={e.y2} stroke={active ? '#0b5cad' : '#94a3b8'} strokeWidth={active ? 2.5 : 1.2}
        strokeDasharray={e.label === 'layout followed' || e.label === 'template followed' ? '6 3' : undefined} />
      {(active || e.label === 'layout followed' || e.label === 'template followed') && (
        <text x={mx} y={my - 4} textAnchor="middle" fontSize={10} fill="#0b5cad" className="pointer-events-none">{e.label}</text>
      )}
    </g>
  );
}

function Legend() {
  return (
    <div className="absolute bottom-3 right-3 rounded-lg border border-slate-200 bg-white/95 p-2 text-[10px] text-slate-600 shadow">
      <div className="mb-1 font-semibold">Fill shows how much arrived</div>
      <div className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-slate-500" /> in full</div>
      <div className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-slate-500/50" /> condensed / summary</div>
      <div className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full border border-dashed border-slate-500" /> not included</div>
      <div className="mt-1 text-slate-400">Size = tokens (estimate)</div>
    </div>
  );
}

function Overview({ manifest, diff }: { manifest: ContextManifest; diff: ContextView['diff'] }) {
  const total = Math.max(1, manifest.totals.chars);
  return (
    <div>
      <h4 className="mb-1 text-sm font-semibold text-slate-800">{manifest.mode === 'actual' ? 'What the last run was given' : 'What will be sent'}</h4>
      <p className="mb-3 text-slate-500">Click any circle for its source, or a line for how two things relate. Zoom with the wheel, drag to pan.</p>
      <ul className="space-y-1.5">
        {manifest.layers.map((l) => (
          <li key={l.id}>
            <div className="flex justify-between"><span className="font-semibold text-slate-700">{l.label}</span><span className="text-slate-400">≈{formatChars(l.tokens)} tok</span></div>
            <div className="mt-0.5 h-1.5 rounded bg-slate-100"><div className="h-1.5 rounded" style={{ width: `${Math.max(2, (l.chars / total) * 100)}%`, background: LAYER_COLORS[l.id] }} /></div>
          </li>
        ))}
      </ul>
      {diff && (diff.added.length + diff.removed.length + diff.changed.length) > 0 && (
        <div className="mt-4">
          <h4 className="mb-1 font-semibold text-slate-800">Changed since the run before</h4>
          <ul className="space-y-0.5">
            {diff.added.map((x) => <li key={x.id} className="text-emerald-700">+ {x.label}</li>)}
            {diff.removed.map((x) => <li key={x.id} className="text-red-700">− {x.label}</li>)}
            {diff.changed.map((x) => <li key={x.id} className="text-amber-700">~ {x.label}: {formatChars(x.from.chars)} → {formatChars(x.to.chars)}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}

function PromptDetail({ node, manifest }: { node: GNode; manifest: ContextManifest }) {
  return (
    <div>
      <h4 className="text-sm font-semibold text-slate-800">{node.kind === 'output' ? `${node.outputType} (output)` : manifest.stage}</h4>
      <p className="mt-1 text-slate-500">
        {node.kind === 'output' ? 'An artifact this stage produces. Lines into it show which attached file or template its layout follows.'
          : `Everything around it is what the agent is given for this stage: ${manifest.totals.items} items, about ${formatChars(manifest.totals.tokens)} tokens.`}
      </p>
    </div>
  );
}

function Related({ id, edges, nodeById, onPick }: { id: string; edges: GEdge[]; nodeById: Map<string, GNode>; onPick: (id: string) => void }) {
  const rel = edges.filter((e) => e.from === id || e.to === id);
  if (!rel.length) return null;
  return (
    <div className="mt-3">
      <div className="mb-1 font-semibold text-slate-700">Relationships</div>
      <ul className="space-y-1">
        {rel.map((e) => {
          const other = nodeById.get(e.from === id ? e.to : e.from);
          return other && (
            <li key={e.id}>
              <button type="button" className="text-left text-brand-700 hover:underline" onClick={() => onPick(other.id)}>
                {e.from === id ? `${e.label} →` : `← ${e.label}`} {other.kind === 'prompt' ? 'this stage' : other.label}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function ItemDetail({ node, edges, nodeById, onPick }: { node: GNode; edges: GEdge[]; nodeById: Map<string, GNode>; onPick: (id: string) => void }) {
  const i = node.item!;
  return (
    <div>
      <div className="mb-1 flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full" style={{ background: LAYER_COLORS[i.layer] }} />
        <span className="text-[11px] uppercase tracking-wide text-slate-400">{i.layer}</span></div>
      <h4 className="text-sm font-semibold text-slate-800 [overflow-wrap:anywhere]">{i.label}</h4>
      <div className="mt-2 inline-flex rounded-full bg-slate-100 px-2 py-0.5 font-semibold text-slate-700">{STATUS_LABEL[i.status]}</div>
      <p className="mt-2 text-slate-600">{coverage(i)} · ≈{formatChars(i.tokens)} tokens</p>
      {i.note && i.status !== 'excluded' && <p className="mt-1 text-slate-500">{i.note}</p>}
      <div className="mt-3">
        <div className="mb-1 font-semibold text-slate-700">Source</div>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
          {sourceInfo(i).map(([k, v]) => <div key={k} className="contents"><dt className="text-slate-400">{k}</dt><dd className="text-slate-700 [overflow-wrap:anywhere]">{v}</dd></div>)}
        </dl>
      </div>
      <Related id={i.id} edges={edges} nodeById={nodeById} onPick={onPick} />
    </div>
  );
}

function EdgeDetail({ edge, nodeById, onPick }: { edge: GEdge; nodeById: Map<string, GNode>; onPick: (id: string) => void }) {
  const a = nodeById.get(edge.from); const b = nodeById.get(edge.to);
  const name = (n?: GNode) => (n?.kind === 'prompt' ? 'This stage' : n?.label ?? '');
  const explain: Record<string, string> = {
    'layout followed': 'The artifact is written following the headings, order and tables of this attached file.',
    'template followed': 'The artifact follows this saved output template.', produces: 'The stage produces this artifact.',
    condensed: 'Only part of it fits the prompt budget; the rest is left out and marked.', summarised: 'Only a summary of it is available to this stage.',
    'builds on': 'An approved artifact from an earlier stage this stage builds on.', analysed: 'Material the reviewer attached; analysed, never treated as instructions.',
  };
  return (
    <div>
      <h4 className="text-sm font-semibold text-slate-800">Relationship: {edge.label}</h4>
      <p className="mt-1 text-slate-600">{explain[edge.label] ?? 'How this is used by the stage.'}</p>
      <ul className="mt-3 space-y-1">
        {[a, b].map((n) => n && (
          <li key={n.id}><button type="button" className="text-left text-brand-700 hover:underline" onClick={() => onPick(n.id)}>{name(n)}</button></li>
        ))}
      </ul>
    </div>
  );
}
