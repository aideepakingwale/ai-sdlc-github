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

export interface GNode {
  id: string; x: number; y: number; r: number; kind: 'prompt' | 'item' | 'output';
  label: string; layer?: string; status?: ItemStatus; item?: ManifestItem; outputType?: string;
}
export interface GEdge { id: string; from: string; to: string; label: string; x1: number; y1: number; x2: number; y2: number }
export interface Graph { nodes: GNode[]; edges: GEdge[]; width: number; height: number }

const TAU = Math.PI * 2;

/** Node radius grows with the tokens it contributes (square-root scale, clamped). */
export function radiusFor(tokens: number): number {
  return Math.max(9, Math.min(26, 8 + Math.sqrt(Math.max(0, tokens)) * 0.55));
}

export interface GraphFilter { layers?: string[]; query?: string; hideExcluded?: boolean }

export function visibleItems(m: ContextManifest, f: GraphFilter = {}): ManifestItem[] {
  const q = (f.query ?? '').trim().toLowerCase();
  return m.layers
    .filter((l) => !f.layers || f.layers.includes(l.id))
    .flatMap((l) => l.items)
    .filter((i) => !(f.hideExcluded && i.status === 'excluded'))
    .filter((i) => !q || `${i.label} ${i.note} ${Object.values(i.source).join(' ')}`.toLowerCase().includes(q));
}

/** Items sit on rings around the prompt, each layer in its own angular sector; outputs on the right. */
export function layoutGraph(m: ContextManifest, f: GraphFilter = {}): Graph {
  const items = visibleItems(m, f);
  const byLayer = m.layers.map((l) => ({ id: l.id, items: items.filter((i) => i.layer === l.id) })).filter((l) => l.items.length);
  const total = byLayer.reduce((n, l) => n + Math.max(1, l.items.length), 0) || 1;
  const gap = 0.12;                                          // radians between layers
  const wedge = Math.PI * 0.55;                              // right side reserved for outputs
  const span = TAU - wedge - gap * byLayer.length;
  const nodes: GNode[] = [{ id: 'prompt', x: 0, y: 0, r: 34, kind: 'prompt', label: m.stage || 'This stage' }];
  let angle = wedge / 2 + gap / 2;
  for (const layer of byLayer) {
    const share = (Math.max(1, layer.items.length) / total) * span;
    layer.items.forEach((item, i) => {
      const a = angle + (share * (i + 0.5)) / layer.items.length;
      const ring = 250 + (i % 2) * 70 + Math.floor(layer.items.length / 14) * 40;      // alternate rings so neighbours do not overlap
      nodes.push({ id: item.id, kind: 'item', x: Math.cos(a) * ring, y: Math.sin(a) * ring, r: radiusFor(item.tokens),
                   label: item.label, layer: item.layer, status: item.status, item });
    });
    angle += share + gap;
  }
  const outs = m.outputs;
  outs.forEach((o, i) => {
    const a = ((i + 0.5) / Math.max(1, outs.length) - 0.5) * (wedge * 0.8);
    nodes.push({ id: o.id, kind: 'output', x: Math.cos(a) * 190, y: Math.sin(a) * 190, r: 16, label: o.type, outputType: o.type });
  });
  const at = new Map(nodes.map((n) => [n.id, n]));
  const edges: GEdge[] = m.edges
    .filter((e) => at.has(e.from) && at.has(e.to))
    .map((e) => {
      const a = at.get(e.from)!; const b = at.get(e.to)!;
      return { ...e, x1: a.x, y1: a.y, x2: b.x, y2: b.y };
    });
  const extent = Math.max(...nodes.map((n) => Math.hypot(n.x, n.y) + n.r), 200) + 40;
  return { nodes, edges, width: extent * 2, height: extent * 2 };
}

/** Everything directly connected to a node (for highlighting and the detail panel). */
export function neighbours(g: Graph, id: string): { nodes: Set<string>; edges: Set<string> } {
  const nodes = new Set<string>([id]); const edges = new Set<string>();
  for (const e of g.edges) {
    if (e.from === id || e.to === id) { nodes.add(e.from); nodes.add(e.to); edges.add(e.id); }
  }
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
