import { describe, expect, it } from 'vitest';
import { describe as describeNode, layoutProject, relations, visibleEdges, type PEdge, type PNode, type ProjectGraphData } from './projectGraph';

const n = (id: string, kind: PNode['kind'], level: number, o: Partial<PNode> = {}): PNode => ({ id, kind, label: id, level, status: 'full', tokens: 0, source: {}, ...o });
const data: ProjectGraphData = {
  levels: 2, totals: { stages: 2, artifacts: 1, attachments: 1, truncated: false },
  nodes: [n('project', 'project', -1), n('stack', 'project', -1),
    n('stage:1', 'stage', 0, { phase: 1, status: 'APPROVED', tokens: 4000, source: { ran: true } }),
    n('stage:2', 'stage', 1, { phase: 2, status: 'NOT_STARTED' }),
    n('att:f1', 'attachment', 0, { phase: 1, label: 'reqs.pdf', source: { stage: 'Requirements' } }),
    n('art:a1', 'artifact', 0, { phase: 1, label: 'PRD: PRD', source: { artifactType: 'PRD', phase: 1, version: 2 } })],
  edges: [{ id: 'f', from: 'stage:1', to: 'stage:2', label: 'feeds' }, { id: 'p', from: 'stage:1', to: 'art:a1', label: 'produced' },
    { id: 'at', from: 'att:f1', to: 'stage:1', label: 'attached' }, { id: 'ap', from: 'project', to: 'stage:2', label: 'applies to' },
    { id: 'b', from: 'art:a1', to: 'stage:2', label: 'builds on' }] as PEdge[],
};

describe('project context graph', () => {
  it('lays out shared context on the left, a column per level, files left of a stage and artifacts below it', () => {
    const l = layoutProject(data);
    const at = (id: string) => l.nodes.find((x) => x.id === id)!;
    expect(at('project').x).toBeLessThan(at('stage:1').x);
    expect(at('stage:1').x).toBeLessThan(at('stage:2').x);
    expect(at('att:f1').x).toBeLessThan(at('stage:1').x);
    expect(at('art:a1').x).toBeGreaterThan(at('stage:1').x);
    expect(at('art:a1').y).toBeGreaterThan(at('stage:1').y);
    expect(l.nodes.every((x) => x.x >= 0 && x.y >= 0 && x.x <= l.width && x.y <= l.height)).toBe(true);
    expect(at('stage:1').r).toBeGreaterThan(at('stage:2').r);                          // bigger when it read more context
  });

  it('can hide whole kinds and search, but always keeps the stages', () => {
    expect(layoutProject(data, new Set(['stage'])).nodes.map((x) => x.id).sort()).toEqual(['stage:1', 'stage:2']);
    expect(layoutProject(data, undefined, 'reqs').nodes.map((x) => x.id)).toContain('att:f1');
    expect(layoutProject(data, undefined, 'reqs').nodes.some((x) => x.id === 'art:a1')).toBe(false);
    expect(layoutProject(data, new Set(['stage'])).edges.map((e) => e.id).sort()).toEqual(['f']);
  });

  it('draws the flow always and the blanket relations only around the node in focus', () => {
    const l = layoutProject(data);
    expect(visibleEdges(l.edges, null).map((e) => e.id).sort()).toEqual(['at', 'f', 'p']);
    expect(visibleEdges(l.edges, 'project').map((e) => e.id)).toContain('ap');
    expect(visibleEdges(l.edges, 'art:a1').map((e) => e.id)).toContain('b');
    expect(visibleEdges(l.edges, 'art:a1').map((e) => e.id)).not.toContain('ap');
  });

  it('says what a node uses and what uses it, in words', () => {
    const r = relations(data, 'stage:2');
    expect(r.uses.map((x) => x.id).sort()).toEqual(['art:a1', 'project', 'stage:1']);
    expect(relations(data, 'art:a1').usedBy.map((x) => x.id)).toEqual(['stage:2']);
    expect(describeNode(data.nodes[2]!)).toBe('approved · last run read ≈4,000 tokens of context');
    expect(describeNode(data.nodes[3]!)).toBe('not started · not run yet');
    expect(describeNode(data.nodes[5]!)).toBe('PRD from stage 1, version 2');
  });

  it('wraps a long pipeline into rows instead of one very wide strip', () => {
    const stages = Array.from({ length: 8 }, (_, i) => n(`stage:${i + 1}`, 'stage', i, { phase: i + 1, status: 'NOT_STARTED' }));
    const l = layoutProject({ levels: 8, totals: { stages: 8, artifacts: 0, attachments: 0, truncated: false }, nodes: stages, edges: [] });
    const at = (id: string) => l.nodes.find((x) => x.id === id)!;
    expect(at('stage:5').y).toBeGreaterThan(at('stage:1').y);                     // the fifth stage starts a new row
    expect(at('stage:5').x).toBe(at('stage:1').x);                                // ...back at the first column
    expect(at('stage:4').y).toBe(at('stage:1').y);
    expect(l.width).toBeLessThan(8 * 460 * 0.7);                                   // narrower than one 8-column strip
  });
});
