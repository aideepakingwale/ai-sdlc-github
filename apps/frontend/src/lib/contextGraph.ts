/**
 * Pure model for the context visualizer: types for the manifest the API returns, a deterministic
 * radial layout (this stage's prompt in the middle, its inputs around it, the artifacts it produces
 * on the right) and the filtering / selection helpers. No React and no DOM, so it is unit-testable.
 * Read-only: nothing here changes what a stage is given.
 */
export type ItemStatus = 'full' | 'condensed' | 'summarised' | 'excluded';

export interface ManifestItem {
  id: string; layer: string; label: string; kind: string; status: ItemStatus;
  chars: number; totalChars: number; tokens: number; source: Record<string, unknown>; note: string;
}
export interface ManifestLayer { id: string; label: string; hint: string; items: ManifestItem[]; chars: number; tokens: number }
export interface ManifestEdge { id: string; from: string; to: string; label: string }
export interface ContextManifest {
  mode: 'preview' | 'actual'; phase: number; stage: string; generatedAt: string; runId?: string; ranAt?: string;
  layers: ManifestLayer[]; outputs: Array<{ id: string; type: string }>; edges: ManifestEdge[];
  /** The stages this one builds on (transitively), with their own dependencies, by stage number. */
  lineage?: Array<{ phase: number; name: string; dependsOn: number[]; direct: boolean }>;
  totals: { chars: number; tokens: number; items: number; condensed: number; excluded: number };
}
export interface ContextDiff {
  added: Array<{ id: string; label: string }>; removed: Array<{ id: string; label: string }>;
  changed: Array<{ id: string; label: string; from: { status: string; chars: number }; to: { status: string; chars: number } }>;
}
export interface ContextView { phase: number; stage: string; preview: ContextManifest; actual: ContextManifest | null; diff: ContextDiff | null }
export interface ContextOverview {
  stages: Array<{ phase: number; stage: string; ranAt: string; totals: ContextManifest['totals']; layers: Array<{ id: string; label: string; tokens: number; items: number }> }>;
}

export const LAYER_COLORS: Record<string, string> = {
  instructions: '#64748b', project: '#0ea5e9', canon: '#8b5cf6', upstream: '#10b981',
  input: '#f59e0b', revision: '#f97316', attached: '#ec4899', retrieved: '#14b8a6',
};
export const STATUS_LABEL: Record<ItemStatus, string> = {
  full: 'In full', condensed: 'Condensed', summarised: 'Summary only', excluded: 'Not included',
};

export interface GraphFilter { layers?: string[]; query?: string; hideExcluded?: boolean }

export type GKind = 'prompt' | 'item' | 'output' | 'group';
export interface GNode {
  id: string; x: number; y: number; r: number; kind: GKind;
  label: string; layer?: string; status?: ItemStatus; item?: ManifestItem; outputType?: string;
  /** group: the box (x, y is its centre) and what it holds. */
  w?: number; h?: number; tokens?: number; count?: number; phase?: number; hint?: string;
  /** item: the group it sits in. */
  parent?: string;
}
export type GEdgeKind = 'depends' | 'feeds' | 'cross' | 'produces';
export interface GEdge {
  id: string; from: string; to: string; label: string; x1: number; y1: number; x2: number; y2: number;
  kind?: GEdgeKind;
  /** the path to draw (a curve, or a route under the columns for long links) and where its label goes */
  d?: string; lx?: number; ly?: number; weight?: number;
}
export interface Graph { nodes: GNode[]; edges: GEdge[]; width: number; height: number }

/** Node radius grows with the tokens it contributes (square-root scale, clamped). */
export function radiusFor(tokens: number): number {
  return Math.max(9, Math.min(26, 8 + Math.sqrt(Math.max(0, tokens)) * 0.55));
}

const GW = 270;            // group box width
const HEAD = 34;           // group header
const ROWH = 32;           // one item row
const GAP = 26;            // vertical gap between boxes in a column
const COLW = 360;          // distance between columns
const ROOTS = ['instructions', 'project', 'canon', 'input', 'attached', 'revision', 'retrieved'];

export function visibleItems(m: ContextManifest, f: GraphFilter = {}): ManifestItem[] {
  const q = (f.query ?? '').trim().toLowerCase();
  return m.layers
    .filter((l) => !f.layers || f.layers.includes(l.id))
    .flatMap((l) => l.items)
    .filter((i) => !(f.hideExcluded && i.status === 'excluded'))
    .filter((i) => !q || `${i.label} ${i.note} ${Object.values(i.source).join(' ')}`.toLowerCase().includes(q));
}

interface Box { id: string; label: string; layer: string; phase?: number; hint: string; items: ManifestItem[]; col: number; x: number; y: number; h: number }

/**
 * A layered, left-to-right graph with real hierarchy:
 *   column 0   the fixed context (instructions, project, templates, your instructions, attachments, history, knowledge)
 *   next       the stages this one builds on, one box each, ordered by their own dependencies (earliest first)
 *   then       the assembled prompt, then the artifacts it produces on the right
 * Boxes hold their items. Links show how things really connect: stage to stage by dependency, each group (and the stages
 * this one directly builds on) into the prompt, and the cross-links (a template or attached file that an artifact follows,
 * the previous version an amendment revises, the files your instructions refer to). Links that would cut across columns
 * run along the bottom so nothing is crossed by a line it has no part in.
 */
export function layoutGraph(m: ContextManifest, f: GraphFilter = {}): Graph {
  const items = visibleItems(m, f);
  const lineage = m.lineage ?? [];
  const known = new Set(lineage.map((l) => l.phase));
  const boxes: Box[] = [];
  const mk = (id: string, label: string, layer: string, hint: string, its: ManifestItem[], col: number, phase?: number): Box =>
    ({ id, label, layer, phase, hint, items: its, col, x: 0, y: 0, h: HEAD + Math.max(1, its.length) * ROWH + 10 });

  const layerInfo = new Map(m.layers.map((l) => [l.id, l] as const));
  for (const id of ROOTS) {
    const its = items.filter((i) => i.layer === id);
    if (its.length) boxes.push(mk(`group:${id}`, layerInfo.get(id)?.label ?? id, id, layerInfo.get(id)?.hint ?? '', its, 0));
  }
  // previous stages: one box per stage in the lineage, columns by dependency depth
  const up = items.filter((i) => i.layer === 'upstream');
  const depth = new Map<number, number>();
  const depthOf = (p: number, seen = new Set<number>()): number => {
    if (depth.has(p)) return depth.get(p)!;
    if (seen.has(p)) return 0;
    seen.add(p);
    const st = lineage.find((l) => l.phase === p);
    const deps = st ? st.dependsOn.filter((x) => known.has(x)) : [];
    const d = deps.length ? 1 + Math.max(...deps.map((x) => depthOf(x, seen))) : 0;
    depth.set(p, d);
    return d;
  };
  lineage.forEach((l) => depthOf(l.phase));
  const stray = up.filter((i) => !known.has(Number(i.source.phase)));
  const filtering = Boolean(f.query?.trim()) || Boolean(f.hideExcluded);
  if (!f.layers || f.layers.includes('upstream')) {
    for (const l of lineage) {
      const its = up.filter((i) => Number(i.source.phase) === l.phase);
      if (!its.length && filtering) continue;                         // a search or filter hides the empty stages too
      boxes.push(mk(`group:stage:${l.phase}`, `Stage ${l.phase} · ${l.name}`, 'upstream', 'An earlier stage this one builds on.', its, 1 + (depth.get(l.phase) ?? 0), l.phase));
    }
  }
  if (stray.length) boxes.push(mk('group:upstream', 'Previous stages', 'upstream', layerInfo.get('upstream')?.hint ?? '', stray, 1));

  const maxCol = Math.max(0, ...boxes.map((b) => b.col));
  const promptCol = maxCol + 1, outCol = maxCol + 2;
  const cols = new Map<number, Box[]>();
  boxes.forEach((b) => cols.set(b.col, [...(cols.get(b.col) ?? []), b]));
  const colH = (c: number) => (cols.get(c) ?? []).reduce((n, b) => n + b.h + GAP, -GAP);
  const outs = m.outputs;
  const OUT_STEP = 64;
  const outH = Math.max(0, outs.length * OUT_STEP);
  const H = Math.max(260, ...[...cols.keys()].map(colH), outH);
  for (const [c, bs] of cols) {                                       // each column centred on the tallest
    let y = (H - colH(c)) / 2;
    for (const b of bs) { b.x = c * COLW; b.y = y; y += b.h + GAP; }
  }

  const nodes: GNode[] = [];
  const at = new Map<string, GNode>();
  const add = (n: GNode) => { nodes.push(n); at.set(n.id, n); };
  for (const b of boxes) {
    const tokens = b.items.filter((i) => i.status !== 'excluded').reduce((n, i) => n + i.tokens, 0);
    add({ id: b.id, kind: 'group', x: b.x + GW / 2, y: b.y + b.h / 2, r: 0, w: GW, h: b.h, label: b.label, layer: b.layer, tokens, count: b.items.length, phase: b.phase, hint: b.hint });
    b.items.forEach((item, i) => add({
      id: item.id, kind: 'item', x: b.x + 22, y: b.y + HEAD + i * ROWH + ROWH / 2, r: Math.min(11, radiusFor(item.tokens) * 0.55),
      label: item.label, layer: item.layer, status: item.status, item, parent: b.id,
    }));
  }
  add({ id: 'prompt', kind: 'prompt', x: promptCol * COLW + 60, y: H / 2, r: 38, label: m.stage || 'This stage' });
  outs.forEach((o, i) => add({ id: o.id, kind: 'output', x: outCol * COLW - 20, y: (H - outH) / 2 + OUT_STEP * (i + 0.5), r: 16, label: o.type, outputType: o.type }));

  // ---- links
  const edges: GEdge[] = [];
  const out = (n: GNode) => (n.kind === 'group' ? n.x + (n.w ?? GW) / 2 : n.x + n.r);
  const inn = (n: GNode) => (n.kind === 'group' ? n.x - (n.w ?? GW) / 2 : n.x - n.r);
  const colOf = (n: GNode): number => {
    if (n.kind === 'prompt') return promptCol;
    if (n.kind === 'output') return outCol;
    const g = n.kind === 'group' ? n : at.get(n.parent ?? '');
    return g ? Math.round((g.x - GW / 2) / COLW) : 0;
  };
  const link = (id: string, from: string, to: string, label: string, kind: GEdgeKind, weight = 1) => {
    const a = at.get(from), b = at.get(to);
    if (!a || !b || edges.some((e) => e.id === id)) return;
    edges.push({ id, from, to, label, kind, weight, x1: out(a), y1: a.y, x2: inn(b), y2: b.y });
  };
  const wt = (id: string) => 1 + Math.min(4, (at.get(id)?.tokens ?? 0) / 800);
  for (const b of boxes) {
    if (b.phase != null) {
      const st = lineage.find((l) => l.phase === b.phase)!;
      for (const d of st.dependsOn) link(`group:stage:${d}->${b.id}`, `group:stage:${d}`, b.id, 'builds on', 'depends');
      if (st.direct) link(`${b.id}->prompt`, b.id, 'prompt', 'builds on', 'feeds', wt(b.id));
    } else {
      link(`${b.id}->prompt`, b.id, 'prompt', b.layer === 'upstream' ? 'builds on' : 'feeds', 'feeds', wt(b.id));
    }
  }
  for (const o of outs) link(`prompt->${o.id}`, 'prompt', o.id, 'produces', 'produces');
  for (const e of m.edges) if (e.to.startsWith('output:') && e.from !== 'prompt') link(e.id, e.from, e.to, e.label, 'cross');   // layout / template followed
  const norm = (t: unknown) => String(t ?? '').toUpperCase().replace(/[^A-Z0-9]/g, '');
  for (const i of items) {
    const target = outs.find((o) => norm(o.type) === norm(i.source.artefactType ?? i.source.artifactType));
    if (!target) continue;
    if (i.layer === 'canon' && i.kind === 'template' && i.status !== 'excluded') link(`${i.id}->${target.id}:applies`, i.id, target.id, 'template applies to', 'cross');
    if (i.layer === 'revision' && i.kind === 'revision') link(`${i.id}->${target.id}:revised`, i.id, target.id, 'revised into', 'cross');
  }
  const instr = items.find((i) => i.id === 'input:instruction');
  if (instr) for (const i of items) if (i.layer === 'attached' && i.status !== 'excluded') link(`${i.id}->${instr.id}`, i.id, instr.id, 'referred to in your instructions', 'cross');

  // ---- routes: neighbouring columns curve directly; longer links run along the bottom
  const bottom = H + 36;
  let lane = 0;
  for (const e of edges) {
    const span = colOf(at.get(e.to)!) - colOf(at.get(e.from)!);
    const dx = Math.max(40, (e.x2 - e.x1) / 2);
    if (span <= 1) {
      e.d = `M${e.x1},${e.y1} C${e.x1 + dx},${e.y1} ${e.x2 - dx},${e.y2} ${e.x2},${e.y2}`;
      e.lx = (e.x1 + e.x2) / 2; e.ly = (e.y1 + e.y2) / 2 - 5;
    } else {
      const yb = bottom + (lane++ % 8) * 11;
      e.d = `M${e.x1},${e.y1} C${e.x1 + 50},${yb} ${e.x2 - 50},${yb} ${e.x2},${e.y2}`;
      e.lx = (e.x1 + e.x2) / 2; e.ly = yb - 3;
    }
  }
  return { nodes, edges, width: outCol * COLW + 240, height: bottom + 8 * 11 + 40 };
}

/** Everything directly connected to a node (for highlighting and the detail panel). */
export function neighbours(g: Graph, id: string): { nodes: Set<string>; edges: Set<string> } {
  const nodes = new Set<string>([id]); const edges = new Set<string>();
  const parent = g.nodes.find((n) => n.id === id)?.parent;           // an item also lights the links of the box it sits in
  for (const e of g.edges) {
    if (e.from === id || e.to === id || (parent && (e.from === parent || e.to === parent))) { nodes.add(e.from); nodes.add(e.to); edges.add(e.id); }
  }
  if (parent) nodes.add(parent);
  for (const n of g.nodes) if (n.parent === id) nodes.add(n.id);        // a box keeps its own items in view
  return { nodes, edges };
}

export function formatChars(n: number): string {
  return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1_000 ? `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1)}k` : String(n);
}

/** Human description of how much of a thing the stage has. */
export function coverage(i: ManifestItem): string {
  if (i.status === 'excluded') return i.note || 'Not included';
  if (i.totalChars > i.chars) return `${formatChars(i.chars)} of ${formatChars(i.totalChars)} characters (${Math.round((i.chars / i.totalChars) * 100)}%)`;
  return `${formatChars(i.chars)} characters`;
}

/** Plain-language lines describing a source (the "source info" of a node). */
export function sourceInfo(i: ManifestItem): Array<[string, string]> {
  const s = i.source as Record<string, unknown>;
  const rows: Array<[string, string]> = [['Kind', String(s.type ?? i.kind)]];
  const add = (label: string, key: string) => { if (s[key] != null && s[key] !== '') rows.push([label, String(s[key])]); };
  add('Stage', 'phase'); add('Artifact type', 'artifactType'); add('Title', 'title'); add('File', 'filename');
  add('Document', 'summary'); add('Scope', 'scope'); add('Applies to', 'artefactType'); add('Prompt', 'id');
  add('Origin', 'origin'); add('Relevance', 'relevance'); add('Decided by', 'decidedBy'); add('Field', 'field');
  return rows;
}
