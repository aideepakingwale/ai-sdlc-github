/**
 * The plan of a stage run as a graph (docs/plan-graph.md). The server sends typed nodes and edges with a `layer` (a column); this file only
 * decides where to draw them, so the same graph serves the plan, the live run and the record of a finished run.
 */
export type NodeType = 'input' | 'check' | 'agent' | 'subagent' | 'skill' | 'tool' | 'artefact' | 'gate';
export type NodeStatus = 'planned' | 'running' | 'done' | 'failed' | 'skipped' | 'reused' | 'deferred' | 'waiting' | 'retrying';
export type EdgeKind = 'flow' | 'reads' | 'delegates' | 'uses' | 'writes';

export interface PlanNode {
  id: string; type: NodeType; label: string; sub: string; status: NodeStatus; layer: number; info: Array<{ k: string; v: string }>;
  retries?: number; custom?: boolean; deferred?: boolean; unavailable?: boolean; unplanned?: boolean; role?: string; model?: string; tokens?: number; detail?: string;
}
export interface PlanEdge { id: string; from: string; to: string; kind: EdgeKind; label: string }
export interface PlanGraph {
  version: number; stage: { seq: number; key: string; name: string; template: number; persona: string | null; reviewerRole: string | null };
  nodes: PlanNode[]; edges: PlanEdge[]; layers: number;
  counts: { agents: number; subAgents: number; skills: number; tools: number; artefacts: number };
  run: { state: string; jobId: string | null; startedAt?: string | null; finishedAt?: string | null; error?: string };
}
export interface PlanGraphResponse {
  mode: 'planned' | 'live' | 'record'; graph: PlanGraph; available: { planned: boolean; run: boolean }; stageStatus: string; canEdit: boolean;
}

export const TYPE_LABEL: Record<NodeType, string> = {
  input: 'Input', check: 'Check', agent: 'Stage agent', subagent: 'Sub-agent', skill: 'Skill', tool: 'Tool', artefact: 'Deliverable', gate: 'Review gate',
};
/** Colours are fixed per kind of node so a person learns them once; status is shown by the badge and the outline, never by the fill. */
export const TYPE_COLOR: Record<NodeType, { fill: string; stroke: string; text: string }> = {
  input: { fill: '#f1f5f9', stroke: '#94a3b8', text: '#334155' },
  check: { fill: '#fef3c7', stroke: '#d97706', text: '#78350f' },
  agent: { fill: '#e0e7ff', stroke: '#4f46e5', text: '#312e81' },
  subagent: { fill: '#ede9fe', stroke: '#7c3aed', text: '#4c1d95' },
  skill: { fill: '#ccfbf1', stroke: '#0d9488', text: '#134e4a' },
  tool: { fill: '#ffedd5', stroke: '#ea580c', text: '#7c2d12' },
  artefact: { fill: '#d1fae5', stroke: '#059669', text: '#064e3b' },
  gate: { fill: '#fce7f3', stroke: '#db2777', text: '#831843' },
};
export const STATUS_LABEL: Record<NodeStatus, string> = {
  planned: 'Planned', running: 'Running', retrying: 'Retrying', done: 'Done', failed: 'Failed', skipped: 'Skipped', reused: 'Reused from last run', deferred: 'Queued until approval', waiting: 'Waiting for the reviewer',
};
export const STATUS_COLOR: Record<NodeStatus, string> = {
  planned: '#94a3b8', running: '#2563eb', retrying: '#d97706', done: '#059669', failed: '#dc2626', skipped: '#64748b', reused: '#0891b2', deferred: '#0ea5e9', waiting: '#7c3aed',
};
export const EDGE_LABEL: Record<EdgeKind, string> = { flow: 'then', reads: 'reads', delegates: 'delegates to', uses: 'may use', writes: 'writes' };

export const NODE_W = 216;
export const NODE_H = 66;
const GAP_X = 84;
const GAP_Y = 18;
const PAD = 32;
const ORDER: NodeType[] = ['input', 'check', 'agent', 'subagent', 'skill', 'tool', 'artefact', 'gate'];

export interface Placed extends PlanNode { x: number; y: number }
export interface PlacedEdge extends PlanEdge { d: string; lx: number; ly: number }
export interface Laid { nodes: Placed[]; edges: PlacedEdge[]; width: number; height: number }

/** Columns by layer; inside a column, nodes of a kind stay together and are ordered by where their inputs sit (a few barycentre sweeps), which keeps most edges from crossing. */
export function layout(g: Pick<PlanGraph, 'nodes' | 'edges'>): Laid {
  const maxLayer = g.nodes.reduce((m, n) => Math.max(m, n.layer), 0);
  const cols: PlanNode[][] = Array.from({ length: maxLayer + 1 }, () => []);
  for (const n of g.nodes) cols[n.layer]!.push(n);
  const rank = (n: PlanNode) => ORDER.indexOf(n.type);
  for (const c of cols) c.sort((a, b) => rank(a) - rank(b) || a.label.localeCompare(b.label));
  const index = new Map<string, number>();
  const settle = () => cols.forEach((c) => c.forEach((n, i) => index.set(n.id, i)));
  settle();
  const preds = new Map<string, string[]>();
  for (const e of g.edges) preds.set(e.to, [...(preds.get(e.to) ?? []), e.from]);
  for (let sweep = 0; sweep < 4; sweep++) {
    for (const c of cols.slice(1)) {
      const score = (n: PlanNode) => {
        const p = (preds.get(n.id) ?? []).map((id) => index.get(id)).filter((v): v is number => v !== undefined);
        return p.length ? p.reduce((a, b) => a + b, 0) / p.length : index.get(n.id)!;
      };
      const base = new Map(c.map((n) => [n.id, score(n)]));
      c.sort((a, b) => rank(a) - rank(b) || base.get(a.id)! - base.get(b.id)! || a.label.localeCompare(b.label));
      c.forEach((n, i) => index.set(n.id, i));
    }
  }
  const tallest = Math.max(1, ...cols.map((c) => c.length));
  const colH = tallest * NODE_H + (tallest - 1) * GAP_Y;
  const placed = new Map<string, Placed>();
  cols.forEach((c, li) => {
    const h = c.length * NODE_H + (c.length - 1) * GAP_Y;
    const top = PAD + (colH - h) / 2;
    c.forEach((n, i) => placed.set(n.id, { ...n, x: PAD + li * (NODE_W + GAP_X), y: top + i * (NODE_H + GAP_Y) }));
  });
  const edges: PlacedEdge[] = [];
  for (const e of g.edges) {
    const a = placed.get(e.from), b = placed.get(e.to);
    if (!a || !b) continue;
    const x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2, x2 = b.x, y2 = b.y + NODE_H / 2;
    const dx = Math.max(24, (x2 - x1) / 2);
    edges.push({ ...e, d: `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`, lx: (x1 + x2) / 2, ly: (y1 + y2) / 2 - 4 });
  }
  return { nodes: [...placed.values()], edges, width: PAD * 2 + (maxLayer + 1) * NODE_W + maxLayer * GAP_X, height: PAD * 2 + colH };
}

/** Everything a node touches, so selecting one dims the rest. */
export function neighbours(g: Pick<PlanGraph, 'edges'>, id: string): { nodes: Set<string>; edges: Set<string> } {
  const nodes = new Set([id]);
  const edges = new Set<string>();
  for (const e of g.edges) if (e.from === id || e.to === id) { nodes.add(e.from); nodes.add(e.to); edges.add(e.id); }
  return { nodes, edges };
}

/** One plain sentence that says what the graph shows. */
export function summarise(g: PlanGraph): string {
  const c = g.counts;
  const lead = g.stage.persona ?? 'The stage agent';
  const parts = [`${lead} leads ${c.subAgents} sub-agent${c.subAgents === 1 ? '' : 's'}`];
  if (c.skills) parts.push(`with ${c.skills} skill${c.skills === 1 ? '' : 's'}`);
  if (c.tools) parts.push(`${c.tools} tool${c.tools === 1 ? '' : 's'}`);
  return `${parts.join(', ')}, writing ${c.artefacts} deliverable${c.artefacts === 1 ? '' : 's'} for ${g.stage.reviewerRole ?? 'the reviewer'} to sign off.`;
}

/** How far a run has got: finished nodes out of the nodes that were expected to do something. */
export function progress(g: PlanGraph): { done: number; total: number; failed: number; retrying: number } {
  const work = steps(g);
  return {
    done: work.filter((n) => n.status === 'done' || n.status === 'reused' || n.status === 'skipped' || n.status === 'deferred').length,
    total: work.length, failed: work.filter((n) => n.status === 'failed').length, retrying: work.filter((n) => n.status === 'retrying').length,
  };
}

/** The steps a person follows as a checklist: everything that does work (inputs and tools that are not connected are left out), in the order they happen. */
export function steps(g: Pick<PlanGraph, 'nodes'>): PlanNode[] {
  return g.nodes.filter((n) => n.type !== 'input' && !n.unavailable).sort((a, b) => a.layer - b.layer || ORDER.indexOf(a.type) - ORDER.indexOf(b.type) || a.label.localeCompare(b.label));
}

/** One short line about where a step is: its state, how often it was retried, and the reason when there is one. */
export function stepLine(n: PlanNode): string {
  const bits: string[] = [STATUS_LABEL[n.status]];
  if (n.retries) bits.push(`retried ${n.retries} time${n.retries === 1 ? '' : 's'}`);
  if (n.detail && (n.status === 'failed' || n.status === 'retrying')) bits.push(n.detail);
  return bits.join(' · ');
}
