import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { api } from '../../api/client';
import {
  EDGE_LABEL, layout, neighbours, NODE_H, NODE_W, progress, STATUS_COLOR, STATUS_LABEL, stepLine, steps, summarise, TYPE_COLOR, TYPE_LABEL,
  type NodeStatus, type NodeType, type Placed, type PlacedEdge, type PlanGraphResponse,
} from '../../lib/planGraph';

const clip = (s: string, n: number): string => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

/**
 * "Current plan": the plan of this stage's run drawn as a graph. Who leads, which sub-agents (built-in and custom, and what they delegate to),
 * which skills and tools, what gets written, the checks and the review gate. Before a run it shows the plan; while a run is active the nodes
 * light up as work starts and finishes; afterwards it keeps the record of what ran. READ-ONLY: nothing here changes the plan.
 */
export default function PlanGraphView({ projectId, seq, stageName, onClose }: { projectId: string; seq: number; stageName: string; onClose: () => void }) {
  const [view, setView] = useState<'auto' | 'planned' | 'run'>('auto');
  const [selected, setSelected] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; px: number; py: number } | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const q = useQuery({
    queryKey: ['plan-graph', projectId, seq, view],
    queryFn: () => api.get<PlanGraphResponse>(`/api/projects/${projectId}/phase/${seq}/plan-graph?view=${view}`),
    // Always watching: a run can start (or end) while the view is open, and the view must pick it up. Quick while a run is live, slower otherwise.
    refetchInterval: (query) => (query.state.data?.mode === 'live' ? 1000 : 4000),
    placeholderData: keepPreviousData,
  });
  const data = q.data;
  const laid = useMemo(() => (data ? layout(data.graph) : null), [data]);
  const byId = useMemo(() => new Map((laid?.nodes ?? []).map((n) => [n.id, n])), [laid]);
  const focus = useMemo(() => (data && selected ? neighbours(data.graph, selected) : null), [data, selected]);
  const sel = selected ? byId.get(selected) : undefined;
  const clamp = (z: number) => Math.min(3, Math.max(0.3, z));

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const prog = data ? progress(data.graph) : null;
  const modeText = data?.mode === 'live' ? 'Live: this run is in progress' : data?.mode === 'record' ? 'Record of the last run' : 'The plan for the next run';
  const source = data?.graph.run.source;
  return createPortal(
    <div className="fixed inset-0 z-[60] flex flex-col bg-slate-100" role="dialog" aria-label={`Current plan: ${stageName}`} data-testid="v2-plan-graph">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 bg-white px-4 py-2">
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-slate-800">Current plan · {stageName}</div>
          <div className="text-[11px] text-slate-500" data-testid="v2-plan-graph-summary">{data ? summarise(data.graph) : 'Building the plan…'}</div>
        </div>
        {data?.mode === 'live' && <span className="animate-pulse rounded-full bg-blue-100 px-2 py-0.5 text-[11px] font-semibold text-blue-700" data-testid="v2-plan-graph-live">Live</span>}
        {data && <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600" data-testid="v2-plan-graph-mode">{modeText}</span>}
        {source === 'reconstructed' && data?.mode === 'record' && (
          <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] text-amber-800" data-testid="v2-plan-graph-reconstructed"
            title="This run ran before its steps were recorded. Each step's mark is rebuilt from what the run saved: its parts, the record of each artefact, and the validation results. Tools and some retries were not recorded.">Rebuilt from what the run saved</span>
        )}
        {prog && data?.mode !== 'planned' && (
          <span className="text-[11px] text-slate-600" data-testid="v2-plan-graph-progress">{prog.done} of {prog.total} steps{prog.retrying ? ` · ${prog.retrying} retrying` : ''}{prog.failed ? ` · ${prog.failed} failed` : ''}</span>
        )}
        <div className="inline-flex overflow-hidden rounded-lg border border-slate-300 text-xs font-semibold" role="group" aria-label="Which graph">
          <button type="button" aria-pressed={data?.mode === 'planned'} onClick={() => setView('planned')} data-testid="v2-plan-graph-planned"
            className={`px-2.5 py-1 ${data?.mode === 'planned' ? 'bg-brand-600 text-white' : 'bg-white text-slate-600'}`}>Plan</button>
          <button type="button" aria-pressed={data?.mode === 'live' || data?.mode === 'record'} disabled={!data?.available.run} onClick={() => setView('run')} data-testid="v2-plan-graph-run"
            className={`px-2.5 py-1 ${data?.mode === 'live' || data?.mode === 'record' ? 'bg-brand-600 text-white' : 'bg-white text-slate-600'} disabled:opacity-40`}
            title={data?.available.run ? 'What the last run did' : 'This stage has not run yet'}>{data?.mode === 'live' ? 'Live run' : 'Last run'}</button>
        </div>
        <button onClick={onClose} className="rounded px-2 py-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700" aria-label="Close the plan" data-testid="v2-plan-graph-close">✕</button>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="relative min-w-0 flex-1 overflow-hidden bg-white">
          {q.isError && <div className="absolute inset-0 flex items-center justify-center p-6 text-sm text-red-700" role="alert">{q.error instanceof Error ? q.error.message : 'The plan could not be loaded'}</div>}
          {!data && !q.isError && <div className="absolute inset-0 flex items-center justify-center text-sm text-slate-400"><span className="animate-pulse">Building the plan…</span></div>}
          {laid && data && (
            <svg ref={svgRef} data-testid="plan-graph-svg" className="h-full w-full cursor-grab touch-none select-none active:cursor-grabbing" viewBox={`0 0 ${laid.width} ${laid.height}`} preserveAspectRatio="xMidYMid meet"
              onWheel={(e) => { e.preventDefault(); setZoom((z) => clamp(z * (e.deltaY < 0 ? 1.12 : 1 / 1.12))); }}
              onPointerDown={(e) => { drag.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y }; (e.currentTarget as SVGSVGElement).setPointerCapture(e.pointerId); }}
              onPointerMove={(e) => { const d = drag.current; if (!d) return; const scale = laid.width / (e.currentTarget.clientWidth || 1) / zoom; setPan({ x: d.px + (e.clientX - d.x) * scale, y: d.py + (e.clientY - d.y) * scale }); }}
              onPointerUp={() => { drag.current = null; }}
              onClick={(e) => { if (e.target === e.currentTarget) setSelected(null); }}>
              <defs>
                {(['#64748b', '#7c3aed', '#0d9488'] as const).map((c) => <marker key={c} id={`pg-arrow-${c.slice(1)}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill={c} /></marker>)}
              </defs>
              <g transform={`scale(${zoom}) translate(${pan.x} ${pan.y})`}>
                {laid.edges.map((e) => <Edge key={e.id} e={e} dim={Boolean(focus) && !focus!.edges.has(e.id)} />)}
                {laid.nodes.map((n) => <Node key={n.id} n={n} dim={Boolean(focus) && !focus!.nodes.has(n.id)} active={selected === n.id} onSelect={() => setSelected(n.id)} />)}
              </g>
            </svg>
          )}
          <div className="absolute bottom-3 left-3 flex flex-col overflow-hidden rounded-lg border border-slate-300 bg-white text-sm shadow">
            <button className="px-2.5 py-1 hover:bg-slate-100" aria-label="Zoom in" onClick={() => setZoom((z) => clamp(z * 1.25))}>+</button>
            <button className="border-y border-slate-200 px-2.5 py-1 hover:bg-slate-100" aria-label="Zoom out" onClick={() => setZoom((z) => clamp(z / 1.25))}>−</button>
            <button className="border-b border-slate-200 px-2.5 py-1 text-[11px] hover:bg-slate-100" aria-label="Fit the whole graph" onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}>Fit</button>
            <button className="px-2.5 py-1 text-[11px] hover:bg-slate-100" aria-label="Show at full size" data-testid="v2-plan-graph-actual"
              onClick={() => { const w = svgRef.current?.clientWidth; if (laid && w) { setZoom(clamp(laid.width / w)); setPan({ x: 0, y: 0 }); } }}>100%</button>
          </div>
          <Legend />
        </div>

        <aside className="w-80 shrink-0 overflow-y-auto border-l border-slate-200 bg-white p-4 text-xs" aria-label="Details" data-testid="v2-plan-graph-details">
          {sel ? <Detail n={sel} graph={data!.graph} byId={byId} onPick={setSelected} /> : <Overview r={data} onPick={setSelected} />}
        </aside>
      </div>
    </div>,
    document.body,
  );
}

/** The mark that says where a step is: a green tick when done, a red cross when it failed, an amber rerun arrow (turning) while it is being tried again, a pulsing dot while it runs. */
function Glyph({ status, r = 8 }: { status: NodeStatus; r?: number }) {
  const color = STATUS_COLOR[status];
  const k = r / 8;
  const mark = { stroke: '#fff', strokeWidth: 1.8, fill: 'none', strokeLinecap: 'round', strokeLinejoin: 'round' } as const;
  return (
    <g transform={`scale(${k})`}>
      <circle r={8} fill={status === 'planned' ? '#fff' : color} stroke={color} strokeWidth={1.5} />
      {status === 'done' && <path d="M-3.4,0.2 L-1.1,2.6 L3.6,-2.6" {...mark} />}
      {status === 'failed' && <path d="M-2.8,-2.8 L2.8,2.8 M2.8,-2.8 L-2.8,2.8" {...mark} />}
      {status === 'retrying' && (
        <g {...mark}>
          <animateTransform attributeName="transform" type="rotate" from="0" to="360" dur="1.4s" repeatCount="indefinite" />
          <path d="M3.4,-1.2 A3.7,3.7 0 1 0 2.4,2.6" />
          <path d="M3.9,-3.6 L3.4,-1.2 L1.1,-1.9" />
        </g>
      )}
      {status === 'running' && <circle r={2.8} fill="#fff"><animate attributeName="r" values="1.6;3.4;1.6" dur="1s" repeatCount="indefinite" /></circle>}
      {status === 'reused' && <path d="M1.8,-3.2 L-1.6,0 L1.8,3.2" {...mark} />}
      {status === 'waiting' && <path d="M0,-3.4 L0,0.6 L2.8,2.2" {...mark} />}
      {status === 'skipped' && <path d="M-3.2,0 L3.2,0" {...mark} />}
      {status === 'deferred' && <circle r={2.2} fill="#fff" />}
    </g>
  );
}

function StatusMark({ status, size = 18 }: { status: NodeStatus; size?: number }) {
  return <svg width={size} height={size} viewBox="-9.5 -9.5 19 19" aria-hidden="true" className="shrink-0"><Glyph status={status} /></svg>;
}

function Node({ n, dim, active, onSelect }: { n: Placed; dim: boolean; active: boolean; onSelect: () => void }) {
  const c = TYPE_COLOR[n.type];
  const st = STATUS_COLOR[n.status];
  const live = n.status === 'running';
  const muted = n.unavailable || n.status === 'skipped';
  return (
    <g role="button" tabIndex={0} aria-label={`${TYPE_LABEL[n.type]}: ${n.label}. ${STATUS_LABEL[n.status]}`} data-node={n.id} data-status={n.status} data-type={n.type}
      className="cursor-pointer outline-none" opacity={dim ? 0.25 : muted ? 0.6 : 1}
      onClick={(e) => { e.stopPropagation(); onSelect(); }} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(); } }} onPointerDown={(e) => e.stopPropagation()}>
      <rect x={n.x} y={n.y} width={NODE_W} height={NODE_H} rx={10} fill={c.fill} stroke={active ? '#0b2a4a' : n.status === 'planned' ? c.stroke : st} strokeWidth={active ? 3 : n.status === 'planned' ? 1.5 : 2.5}
        strokeDasharray={n.unavailable || n.unplanned ? '5 3' : undefined}>
        {live && <animate attributeName="stroke-opacity" values="1;0.3;1" dur="1.2s" repeatCount="indefinite" />}
      </rect>
      <text x={n.x + 12} y={n.y + 26} fontSize={14.5} fontWeight={700} fill={c.text}>{clip(n.label, 23)}</text>
      <text x={n.x + 12} y={n.y + 47} fontSize={12} fill="#475569">{clip(n.sub || TYPE_LABEL[n.type], 31)}</text>
      <g transform={`translate(${n.x + NODE_W - 16}, ${n.y + 16})`} aria-hidden="true" data-glyph={n.status}>{n.type === 'skill' && n.status === 'planned' ? null : <Glyph status={n.status} />}</g>
      {n.retries ? <text x={n.x + 12} y={n.y + NODE_H - 8} fontSize={10.5} fontWeight={700} fill={STATUS_COLOR.retrying} data-testid="plan-node-retries">↻ retried {n.retries}×</text> : null}
      {n.custom && <text x={n.x + NODE_W - 10} y={n.y + NODE_H - 9} textAnchor="end" fontSize={10} fontWeight={700} fill={c.stroke}>CUSTOM</text>}
    </g>
  );
}

const EDGE_STYLE: Record<string, { stroke: string; dash?: string; w: number; marker: string; labelled: boolean }> = {
  flow: { stroke: '#64748b', w: 1.6, marker: 'pg-arrow-64748b', labelled: false },
  writes: { stroke: '#64748b', w: 1.6, marker: 'pg-arrow-64748b', labelled: false },
  reads: { stroke: '#94a3b8', dash: '2 3', w: 1.2, marker: 'pg-arrow-64748b', labelled: false },
  delegates: { stroke: '#7c3aed', dash: '6 3', w: 2, marker: 'pg-arrow-7c3aed', labelled: true },
  uses: { stroke: '#0d9488', dash: '1 4', w: 1.6, marker: 'pg-arrow-0d9488', labelled: false },
};

function Edge({ e, dim }: { e: PlacedEdge; dim: boolean }) {
  const st = EDGE_STYLE[e.kind] ?? EDGE_STYLE.flow!;
  return (
    <g opacity={dim ? 0.08 : 1} data-edge={e.id} data-kind={e.kind}>
      <path d={e.d} fill="none" stroke={st.stroke} strokeWidth={st.w} strokeDasharray={st.dash} markerEnd={`url(#${st.marker})`} />
      {st.labelled && e.label && <text x={e.lx} y={e.ly} textAnchor="middle" fontSize={11} fill={st.stroke} style={{ paintOrder: 'stroke', stroke: '#fff', strokeWidth: 3 }}>when {clip(e.label, 28)}</text>}
    </g>
  );
}

function Legend() {
  const types = (Object.keys(TYPE_COLOR) as NodeType[]);
  return (
    <div className="absolute bottom-3 right-3 max-w-[22rem] rounded-lg border border-slate-200 bg-white/95 p-2 text-[10px] text-slate-600 shadow" data-testid="v2-plan-graph-legend">
      <div className="mb-1 font-semibold">Reading the graph</div>
      <div className="flex flex-wrap gap-x-2 gap-y-0.5">
        {types.map((t) => <span key={t} className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-sm border" style={{ background: TYPE_COLOR[t].fill, borderColor: TYPE_COLOR[t].stroke }} />{TYPE_LABEL[t]}</span>)}
      </div>
      <div className="mt-1 flex flex-wrap gap-x-2 gap-y-0.5">
        {(['done', 'running', 'retrying', 'failed', 'skipped', 'waiting'] as const).map((s) => <span key={s} className="flex items-center gap-1"><StatusMark status={s} size={13} />{STATUS_LABEL[s]}</span>)}
      </div>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
        <span className="flex items-center gap-1"><span className="w-5 border-t-2 border-slate-500" /> then</span>
        <span className="flex items-center gap-1"><span className="w-5 border-t-2 border-dashed border-violet-600" /> delegates to</span>
        <span className="flex items-center gap-1"><span className="w-5 border-t-2 border-dotted border-teal-600" /> may use</span>
        <span className="flex items-center gap-1"><span className="w-5 border-t border-dotted border-slate-400" /> reads</span>
      </div>
      <div className="mt-1 text-slate-400">Click a box for details. Wheel to zoom, drag to pan.</div>
    </div>
  );
}

function Overview({ r, onPick }: { r: PlanGraphResponse | undefined; onPick: (id: string) => void }) {
  if (!r) return null;
  const c = r.graph.counts;
  return (
    <div>
      <h4 className="mb-1 text-sm font-semibold text-slate-800">{r.mode === 'live' ? 'This run, as it happens' : r.mode === 'record' ? 'What the last run did' : 'What the next run will do'}</h4>
      <p className="mb-3 text-slate-500">Read it left to right: what goes in, the stage agent and the sub-agents it leads, what they write, the checks, and the reviewer who signs it off.</p>
      <dl className="grid grid-cols-2 gap-2">
        {([['Agents', c.agents], ['Sub-agents', c.subAgents], ['Skills', c.skills], ['Tools', c.tools], ['Deliverables', c.artefacts]] as const).map(([k, v]) => (
          <div key={k} className="rounded-lg bg-slate-50 p-2"><dt className="text-slate-500">{k}</dt><dd className="text-base font-semibold text-slate-800" data-testid={`v2-plan-graph-count-${k.toLowerCase()}`}>{v}</dd></div>
        ))}
      </dl>
      {r.graph.run.error && <p className="mt-3 rounded-lg bg-red-50 p-2 text-red-700" role="alert">The run stopped: {r.graph.run.error}</p>}
      <h4 className="mb-1 mt-4 text-sm font-semibold text-slate-800">Steps</h4>
      <ol className="space-y-0.5" data-testid="v2-plan-steps">
        {steps(r.graph).map((n) => (
          <li key={n.id}>
            <button type="button" onClick={() => onPick(n.id)} data-testid="v2-plan-step" data-status={n.status} data-step={n.id}
              className="flex w-full items-start gap-2 rounded-lg px-1.5 py-1 text-left hover:bg-slate-50">
              <span className="mt-0.5"><StatusMark status={n.status} /></span>
              <span className="min-w-0"><span className="block truncate font-medium text-slate-800">{n.label}</span><span className="block text-slate-500">{stepLine(n)}</span></span>
            </button>
          </li>
        ))}
      </ol>
      {r.mode === 'planned' && <p className="mt-3 text-slate-500">This is the plan, built from the stage, its agents and what is connected to this project. It costs no tokens. Nothing has run yet.</p>}
    </div>
  );
}

function Detail({ n, graph, byId, onPick }: { n: Placed; graph: PlanGraphResponse['graph']; byId: Map<string, Placed>; onPick: (id: string) => void }) {
  const into = graph.edges.filter((e) => e.to === n.id), out = graph.edges.filter((e) => e.from === n.id);
  const link = (id: string, kind: string, label: string) => {
    const o = byId.get(id);
    return o ? <li key={`${id}-${kind}`}><button type="button" className="text-left text-brand-700 hover:underline" onClick={() => onPick(id)}>{o.label}</button><span className="text-slate-400"> · {EDGE_LABEL[kind as keyof typeof EDGE_LABEL] ?? kind}{label ? ` (${clip(label, 30)})` : ''}</span></li> : null;
  };
  return (
    <div data-testid="v2-plan-graph-node">
      <div className="mb-1 flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-sm border" style={{ background: TYPE_COLOR[n.type].fill, borderColor: TYPE_COLOR[n.type].stroke }} />
        <span className="text-[11px] uppercase tracking-wide text-slate-400">{n.custom ? 'Custom ' : ''}{TYPE_LABEL[n.type]}</span></div>
      <h4 className="text-sm font-semibold text-slate-800">{n.label}</h4>
      <p className="mt-0.5 text-slate-500">{n.sub}</p>
      <p className="mt-2 flex items-center gap-1.5"><StatusMark status={n.status} size={16} /><b data-testid="v2-plan-graph-node-status">{STATUS_LABEL[n.status]}</b>
        {n.retries ? <span className="text-slate-500">· retried {n.retries} time{n.retries === 1 ? '' : 's'}</span> : null}
        {n.unplanned && <span className="text-slate-500">· it ran but was not in the plan</span>}</p>
      {n.detail && <p className="mt-1 rounded-lg bg-slate-50 p-2 text-slate-700">{n.detail}</p>}
      {(n.model || n.tokens != null) && (
        <p className="mt-2 text-slate-600">{n.model ? <>Model <b>{n.model}</b></> : null}{n.model && n.tokens != null ? ' · ' : ''}{n.tokens != null ? <>{n.tokens.toLocaleString('en-GB')} tokens</> : null}</p>
      )}
      {n.info.length > 0 && <dl className="mt-3 space-y-1.5">{n.info.map((i) => <div key={i.k}><dt className="text-[10px] uppercase tracking-wide text-slate-400">{i.k}</dt><dd className="text-slate-700">{i.v}</dd></div>)}</dl>}
      {into.length > 0 && <><h5 className="mb-1 mt-3 font-semibold text-slate-700">Comes from</h5><ul className="space-y-0.5">{into.map((e) => link(e.from, e.kind, e.label))}</ul></>}
      {out.length > 0 && <><h5 className="mb-1 mt-3 font-semibold text-slate-700">Leads to</h5><ul className="space-y-0.5">{out.map((e) => link(e.to, e.kind, e.label))}</ul></>}
    </div>
  );
}
