import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { api } from '../api/client';
import {
  KIND_COLOR, KIND_LABEL, STAGE_STATUS_COLOR, describe, layoutProject, relations, visibleEdges,
  type PNodeKind, type Placed, type PlacedEdge, type ProjectGraphData,
} from '../lib/projectGraph';

/**
 * The whole project's context, read-only: shared project context on the left, then the pipeline stage by
 * stage with the files attached to each, the artifacts each produced and which later stages build on
 * them. Zoom, pan, search, hide kinds, click any node for what it uses and what uses it. Needs only the
 * project's read permission.
 */
export default function ProjectContextGraph({ projectId, onClose, onOpenStage }: {
  projectId: string; onClose: () => void; onOpenStage?: (phase: number) => void;
}) {
  const q = useQuery({
    queryKey: ['context-graph', projectId],
    queryFn: () => api.get<ProjectGraphData>(`/api/projects/${projectId}/context/graph`),
    staleTime: 0,
  });
  const [kinds, setKinds] = useState<Set<PNodeKind>>(new Set(Object.keys(KIND_LABEL) as PNodeKind[]));
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; px: number; py: number } | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const data = q.data;
  const layout = useMemo(() => (data ? layoutProject(data, kinds, query) : null), [data, kinds, query]);
  const edges: PlacedEdge[] = useMemo(() => (layout ? visibleEdges(layout.edges, selected) : []), [layout, selected]);
  const sel = layout?.nodes.find((n) => n.id === selected);
  const rel = data && selected ? relations(data, selected) : null;
  const linked = useMemo(() => {
    if (!selected || !layout) return null;
    const s = new Set<string>([selected]);
    layout.edges.forEach((e) => { if (e.from === selected || e.to === selected) { s.add(e.from); s.add(e.to); } });
    return s;
  }, [layout, selected]);
  const clamp = (z: number) => Math.min(4, Math.max(0.25, z));
  const toggle = (k: PNodeKind) => setKinds((cur) => { const n = new Set(cur); if (n.has(k)) n.delete(k); else n.add(k); return n; });

  return createPortal(
    <div className="fixed inset-0 z-[60] flex flex-col bg-slate-100" role="dialog" aria-label="Project context graph">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 bg-white px-4 py-2">
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-slate-800">Project context</div>
          <div className="text-[11px] text-slate-400">
            {data ? `${data.totals.stages} stages · ${data.totals.artifacts} artifacts · ${data.totals.attachments} attached files${data.totals.truncated ? ' · showing the newest artifacts only' : ''} · read-only` : 'Loading…'}
          </div>
        </div>
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search the project…" aria-label="Search the project"
          className="w-48 rounded-lg border border-slate-300 px-2 py-1 text-xs focus:border-brand-400 focus:outline-none" />
        <button onClick={onClose} className="rounded px-2 py-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700" aria-label="Close project graph">✕</button>
      </div>
      <div className="flex flex-wrap items-center gap-1.5 border-b border-slate-200 bg-white px-4 py-1.5">
        {(Object.keys(KIND_LABEL) as PNodeKind[]).map((k) => {
          const on = kinds.has(k);
          return (
            <button key={k} type="button" disabled={k === 'stage'} onClick={() => toggle(k)} aria-pressed={on}
              className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-semibold ${on ? 'border-slate-300 bg-white text-slate-700' : 'border-slate-200 bg-slate-50 text-slate-400'}`}>
              <span className="h-2 w-2 rounded-full" style={{ background: on ? KIND_COLOR[k] : '#cbd5e1' }} />{KIND_LABEL[k]}
            </button>
          );
        })}
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="relative min-w-0 flex-1 overflow-hidden bg-white">
          {q.isError && <div className="absolute inset-0 flex items-center justify-center text-sm text-red-600">Could not load the project context.</div>}
          {layout && (
            <svg data-testid="project-graph" className="h-full w-full cursor-grab touch-none select-none active:cursor-grabbing"
              viewBox={`0 0 ${layout.width} ${layout.height}`} preserveAspectRatio="xMidYMid meet"
              onWheel={(e) => { e.preventDefault(); setZoom((z) => clamp(z * (e.deltaY < 0 ? 1.12 : 1 / 1.12))); }}
              onPointerDown={(e) => { drag.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y }; (e.currentTarget as SVGSVGElement).setPointerCapture(e.pointerId); }}
              onPointerMove={(e) => { const d = drag.current; if (!d) return; const k = layout.width / (e.currentTarget.clientWidth || 1) / zoom; setPan({ x: d.px + (e.clientX - d.x) * k, y: d.py + (e.clientY - d.y) * k }); }}
              onPointerUp={() => { drag.current = null; }}
              onClick={(e) => { if (e.target === e.currentTarget) setSelected(null); }}>
              <g transform={`scale(${zoom}) translate(${pan.x} ${pan.y})`}>
                {edges.map((e) => <EdgeLine key={e.id} e={e} dim={Boolean(linked) && !(linked!.has(e.from) && linked!.has(e.to))} />)}
                {layout.nodes.map((n) => <NodeDot key={n.id} n={n} active={selected === n.id} dim={Boolean(linked) && !linked!.has(n.id)} onSelect={() => setSelected(n.id)} />)}
              </g>
            </svg>
          )}
          <div className="absolute bottom-3 left-3 flex flex-col overflow-hidden rounded-lg border border-slate-300 bg-white text-sm shadow">
            <button className="px-2.5 py-1 hover:bg-slate-100" aria-label="Zoom in" onClick={() => setZoom((z) => clamp(z * 1.25))}>+</button>
            <button className="border-y border-slate-200 px-2.5 py-1 hover:bg-slate-100" aria-label="Zoom out" onClick={() => setZoom((z) => clamp(z / 1.25))}>−</button>
            <button className="px-2.5 py-1 text-[11px] hover:bg-slate-100" aria-label="Reset view" onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}>Fit</button>
          </div>
        </div>
        <aside className="w-80 shrink-0 overflow-y-auto border-l border-slate-200 bg-white p-4 text-xs" aria-label="Details">
          {!sel && (
            <div>
              <h4 className="mb-1 text-sm font-semibold text-slate-800">How context flows</h4>
              <p className="text-slate-500">Shared project context (left) applies to the stages. Each stage reads the artifacts of the stages before it and the files attached to it. Click any circle to see what it uses and what uses it; the lines for “applies to” and “builds on” appear for the selected item.</p>
              <ul className="mt-3 space-y-1">
                {(layout?.nodes ?? []).filter((n) => n.kind === 'stage').map((s) => (
                  <li key={s.id}><button type="button" className="flex w-full items-center gap-2 text-left hover:underline" onClick={() => setSelected(s.id)}>
                    <span className="h-2 w-2 rounded-full" style={{ background: STAGE_STATUS_COLOR[s.status] ?? '#94a3b8' }} />
                    <span className="flex-1 truncate text-slate-700">{s.label}</span>
                    <span className="text-slate-400">{s.tokens ? `≈${s.tokens.toLocaleString()} tok` : 'not run'}</span></button></li>
                ))}
              </ul>
            </div>
          )}
          {sel && rel && (
            <div>
              <div className="mb-1 flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full" style={{ background: KIND_COLOR[sel.kind] }} />
                <span className="text-[11px] uppercase tracking-wide text-slate-400">{KIND_LABEL[sel.kind]}</span></div>
              <h4 className="text-sm font-semibold text-slate-800 [overflow-wrap:anywhere]">{sel.label}</h4>
              <p className="mt-1 text-slate-600">{describe(sel)}</p>
              {sel.kind === 'stage' && sel.phase != null && onOpenStage && (
                <button type="button" className="mt-2 rounded-lg border border-brand-300 bg-brand-50 px-2.5 py-1 font-semibold text-brand-700 hover:bg-brand-100"
                  onClick={() => { onOpenStage(sel.phase!); onClose(); }}>Open this stage</button>
              )}
              <RelList title="Uses" items={rel.uses} onPick={setSelected} />
              <RelList title="Used by" items={rel.usedBy} onPick={setSelected} />
            </div>
          )}
        </aside>
      </div>
    </div>,
    document.body,
  );
}

function RelList({ title, items, onPick }: { title: string; items: Array<{ id: string; label: string }>; onPick: (id: string) => void }) {
  if (!items.length) return null;
  const shown = items.slice(0, 40);
  return (
    <div className="mt-3">
      <div className="mb-1 font-semibold text-slate-700">{title} <span className="font-normal text-slate-400">{items.length}</span></div>
      <ul className="space-y-0.5">
        {shown.map((n) => <li key={n.id}><button type="button" className="text-left text-brand-700 hover:underline [overflow-wrap:anywhere]" onClick={() => onPick(n.id)}>{n.label}</button></li>)}
        {items.length > shown.length && <li className="text-slate-400">+ {items.length - shown.length} more</li>}
      </ul>
    </div>
  );
}

function NodeDot({ n, active, dim, onSelect }: { n: Placed; active: boolean; dim: boolean; onSelect: () => void }) {
  const fill = n.kind === 'stage' ? (STAGE_STATUS_COLOR[n.status] ?? '#94a3b8') : KIND_COLOR[n.kind];
  const excluded = n.status === 'excluded';
  return (
    <g role="button" tabIndex={0} aria-label={n.label} data-node={n.id} opacity={dim ? 0.2 : 1} className="cursor-pointer outline-none focus:outline-none"
      onClick={(e) => { e.stopPropagation(); onSelect(); }} onKeyDown={(e) => { if (e.key === 'Enter') onSelect(); }} onPointerDown={(e) => e.stopPropagation()}>
      <circle cx={n.x} cy={n.y} r={n.r} fill={excluded ? '#fff' : fill} stroke={fill} strokeWidth={active ? 4 : 1.5} strokeDasharray={excluded ? '4 3' : undefined} />
      {n.kind === 'stage' && <text x={n.x} y={n.y + 4} textAnchor="middle" fontSize={14} fill="#fff" fontWeight={700}>{n.phase}</text>}
      {n.kind === 'artifact' || n.kind === 'attachment' ? (
        <text x={n.kind === 'artifact' ? n.x + n.r + 6 : n.x - n.r - 6} y={n.y + 4} textAnchor={n.kind === 'artifact' ? 'start' : 'end'} fontSize={12} fill="#334155">
          {n.label.length > 22 ? `${n.label.slice(0, 21)}…` : n.label}
        </text>
      ) : (
        <text x={n.x} y={n.y + n.r + 15} textAnchor="middle" fontSize={n.kind === 'stage' ? 15 : 12} fill="#334155" fontWeight={n.kind === 'stage' ? 600 : 400}>
          {n.label.length > 30 ? `${n.label.slice(0, 29)}…` : n.label}
        </text>
      )}
    </g>
  );
}

function EdgeLine({ e, dim }: { e: PlacedEdge; dim: boolean }) {
  const flow = e.label === 'feeds';
  return (
    <line data-edge={e.id} x1={e.x1} y1={e.y1} x2={e.x2} y2={e.y2} opacity={dim ? 0.12 : 1}
      stroke={flow ? '#0b5cad' : '#cbd5e1'} strokeWidth={flow ? 2.2 : 1.2} strokeDasharray={e.label === 'applies to' || e.label === 'builds on' ? '5 3' : undefined} />
  );
}
