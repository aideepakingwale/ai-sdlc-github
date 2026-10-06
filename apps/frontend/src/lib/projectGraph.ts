/**
 * Pure model for the project-level context graph: types for what the API returns, a left-to-right layout
 * (shared project context, then one column per workflow level; each stage with its attached files on its
 * left and its artifacts below) and the helpers for filtering, focusing and describing nodes.
 * Read-only; no React and no DOM, so it is unit-testable.
 */
export type PNodeKind = 'stage' | 'artifact' | 'attachment' | 'project' | 'canon';
export interface PNode {
  id: string; kind: PNodeKind; label: string; level: number; status: string; tokens: number; phase?: number;
  source: Record<string, unknown>;
}
export interface PEdge { id: string; from: string; to: string; label: string }
export interface ProjectGraphData {
  nodes: PNode[]; edges: PEdge[]; levels: number;
  totals: { stages: number; artifacts: number; attachments: number; truncated: boolean };
}
export interface Placed extends PNode { x: number; y: number; r: number }
export interface PlacedEdge extends PEdge { x1: number; y1: number; x2: number; y2: number }
export interface PLayout { nodes: Placed[]; edges: PlacedEdge[]; width: number; height: number }

export const KIND_LABEL: Record<PNodeKind, string> = {
  stage: 'Stages', artifact: 'Artifacts', attachment: 'Attached files', project: 'Project context', canon: 'Standards & templates',
};
export const KIND_COLOR: Record<PNodeKind, string> = {
  stage: '#0b2a4a', artifact: '#10b981', attachment: '#ec4899', project: '#0ea5e9', canon: '#8b5cf6',
};
export const STAGE_STATUS_COLOR: Record<string, string> = {
  APPROVED: '#059669', PENDING_REVIEW: '#d97706', IN_PROGRESS: '#2563eb', AMEND_REQUESTED: '#ea580c', ESCALATED: '#dc2626', NOT_STARTED: '#94a3b8',
};

const COL = 460;
const ROW = 42;

/** Edges that would blanket the canvas (shared context to every stage, every artifact to every downstream
 *  stage) are only drawn for the node in focus. */
export const QUIET_EDGES = new Set(['applies to', 'builds on']);

export function layoutProject(data: ProjectGraphData, visible: Set<PNodeKind> = new Set(Object.keys(KIND_LABEL) as PNodeKind[]), query = ''): PLayout {
  const q = query.trim().toLowerCase();
  const keep = (n: PNode) => n.kind === 'stage' || (visible.has(n.kind) && (!q || `${n.label} ${Object.values(n.source).join(' ')}`.toLowerCase().includes(q)));
  const nodes = data.nodes.filter(keep);
  const ids = new Set(nodes.map((n) => n.id));
  const placed: Placed[] = [];

  nodes.filter((n) => n.level < 0).forEach((n, i) => placed.push({ ...n, x: 0, y: i * 90, r: 18 }));

  const stagesByLevel = new Map<number, Placed[]>();
  for (let level = 0; level < data.levels; level++) {
    let y = 0;
    for (const s of nodes.filter((n) => n.kind === 'stage' && n.level === level)) {
      const atts = nodes.filter((n) => n.kind === 'attachment' && n.phase === s.phase);
      const arts = nodes.filter((n) => n.kind === 'artifact' && n.phase === s.phase);
      const x = (level + 1) * COL;
      const p: Placed = { ...s, x, y, r: 28 + Math.min(12, Math.sqrt(s.tokens) / 8) };
      placed.push(p);
      atts.forEach((a, i) => placed.push({ ...a, x: x - 40, y: y + 84 + i * ROW, r: 11 }));
      arts.forEach((a, i) => placed.push({ ...a, x: x + 40, y: y + 84 + i * ROW, r: 11 }));
      y += 100 + Math.max(atts.length, arts.length) * ROW + 30;
      stagesByLevel.set(level, [...(stagesByLevel.get(level) ?? []), p]);
    }
  }
  const at = new Map(placed.map((n) => [n.id, n]));
  const edges: PlacedEdge[] = data.edges
    .filter((e) => ids.has(e.from) && ids.has(e.to) && at.has(e.from) && at.has(e.to))
    .map((e) => { const a = at.get(e.from)!; const b = at.get(e.to)!; return { ...e, x1: a.x, y1: a.y, x2: b.x, y2: b.y }; });
  const xs = placed.map((n) => n.x); const ys = placed.map((n) => n.y);
  const minX = Math.min(0, ...xs) - 220; const maxX = Math.max(0, ...xs) + 220;
  const minY = Math.min(0, ...ys) - 60; const maxY = Math.max(0, ...ys) + 120;
  // shift so the layout starts at the origin
  const shifted = placed.map((n) => ({ ...n, x: n.x - minX, y: n.y - minY }));
  return {
    nodes: shifted, width: maxX - minX, height: maxY - minY,
    edges: edges.map((e) => ({ ...e, x1: e.x1 - minX, y1: e.y1 - minY, x2: e.x2 - minX, y2: e.y2 - minY })),
  };
}

/** What is drawn: always the flow (feeds / produced / attached); the blanket relations only around the focus. */
export function visibleEdges(edges: PlacedEdge[], focusId: string | null): PlacedEdge[] {
  return edges.filter((e) => !QUIET_EDGES.has(e.label) || (focusId !== null && (e.from === focusId || e.to === focusId)));
}

/** Plain-language relationships of a node: what it uses and what uses it. */
export function relations(data: ProjectGraphData, id: string): { uses: PNode[]; usedBy: PNode[] } {
  const by = new Map(data.nodes.map((n) => [n.id, n]));
  const uses: PNode[] = []; const usedBy: PNode[] = [];
  for (const e of data.edges) {
    if (e.to === id && by.has(e.from)) uses.push(by.get(e.from)!);
    if (e.from === id && by.has(e.to)) usedBy.push(by.get(e.to)!);
  }
  return { uses, usedBy };
}

export function describe(n: PNode): string {
  switch (n.kind) {
    case 'stage': return n.source.ran ? `${n.status.replace(/_/g, ' ').toLowerCase()} · last run read ≈${n.tokens.toLocaleString()} tokens of context` : `${n.status.replace(/_/g, ' ').toLowerCase()} · not run yet`;
    case 'artifact': return `${n.source.artifactType} from stage ${n.source.phase}, version ${n.source.version}`;
    case 'attachment': return `Uploaded to ${n.source.stage}`;
    case 'canon': return String(n.source.names ?? 'Applies to the stages it covers');
    default: return String(n.source.description || n.source.type || '');
  }
}
