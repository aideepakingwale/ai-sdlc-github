import { describe, expect, it } from 'vitest';
import { coverage, layoutGraph, neighbours, radiusFor, sourceInfo, visibleItems, type ContextManifest, type ManifestItem } from './contextGraph';

const item = (id: string, layer: string, over: Partial<ManifestItem> = {}): ManifestItem => ({
  id, layer, label: id, kind: 'item', status: 'full', chars: 400, totalChars: 400, tokens: 100, source: {}, note: '', ...over });

const m: ContextManifest = {
  mode: 'preview', phase: 2, stage: 'Solution', generatedAt: '', outputs: [{ id: 'output:HLD', type: 'HLD' }],
  layers: [
    { id: 'upstream', label: 'Previous stages', hint: '', chars: 0, tokens: 0, items: [item('u1', 'upstream', { label: '[Stage 1] PRD', status: 'condensed', chars: 1200, totalChars: 5000, source: { phase: 1, artifactType: 'PRD' } }), item('u2', 'upstream')] },
    { id: 'attached', label: 'Attached', hint: '', chars: 0, tokens: 0, items: [item('a1', 'attached', { label: 'spec.docx', status: 'excluded', note: 'Binary' })] },
  ],
  edges: [{ id: 'u1->prompt', from: 'u1', to: 'prompt', label: 'builds on' }, { id: 'a1->output:HLD', from: 'a1', to: 'output:HLD', label: 'layout followed' }, { id: 'prompt->output:HLD', from: 'prompt', to: 'output:HLD', label: 'produces' }],
  totals: { chars: 0, tokens: 0, items: 3, condensed: 1, excluded: 1 },
};

describe('context graph', () => {
  it('puts the prompt in the middle, every item and output on the canvas and only links between visible nodes', () => {
    const g = layoutGraph(m);
    expect(g.nodes.find((n) => n.kind === 'prompt')).toMatchObject({ x: 0, y: 0 });
    expect(g.nodes.map((n) => n.id).sort()).toEqual(['a1', 'output:HLD', 'prompt', 'u1', 'u2']);
    expect(g.edges).toHaveLength(3);
    const out = g.nodes.find((n) => n.id === 'output:HLD')!;
    expect(out.x).toBeGreaterThan(0);                                            // outputs on the right
    for (const n of g.nodes.filter((x) => x.kind === 'item')) expect(Math.hypot(n.x, n.y)).toBeGreaterThan(200);
    const hidden = layoutGraph(m, { hideExcluded: true });
    expect(hidden.nodes.some((n) => n.id === 'a1')).toBe(false);
    expect(hidden.edges.every((e) => e.from !== 'a1' && e.to !== 'a1')).toBe(true);
  });

  it('is deterministic and keeps nodes from sitting on top of each other', () => {
    const g1 = layoutGraph(m); const g2 = layoutGraph(m);
    expect(g1).toEqual(g2);
    const pts = g1.nodes.filter((n) => n.kind === 'item');
    for (let i = 0; i < pts.length; i++) for (let j = i + 1; j < pts.length; j++) {
      expect(Math.hypot(pts[i]!.x - pts[j]!.x, pts[i]!.y - pts[j]!.y)).toBeGreaterThan(pts[i]!.r + pts[j]!.r);
    }
  });

  it('filters by layer and by search over label, note and source', () => {
    expect(visibleItems(m, { layers: ['attached'] }).map((i) => i.id)).toEqual(['a1']);
    expect(visibleItems(m, { query: 'prd' }).map((i) => i.id)).toEqual(['u1']);
    expect(visibleItems(m, { query: 'binary' }).map((i) => i.id)).toEqual(['a1']);
    expect(visibleItems(m, { hideExcluded: true })).toHaveLength(2);
  });

  it('finds a node\'s relationships for highlighting', () => {
    const g = layoutGraph(m);
    const n = neighbours(g, 'output:HLD');
    expect([...n.nodes].sort()).toEqual(['a1', 'output:HLD', 'prompt']);
    expect([...n.edges].sort()).toEqual(['a1->output:HLD', 'prompt->output:HLD']);
  });

  it('sizes nodes by tokens within bounds and describes coverage and sources in plain words', () => {
    expect(radiusFor(0)).toBe(9); expect(radiusFor(10_000_000)).toBe(26);
    expect(radiusFor(4_000)).toBeGreaterThan(radiusFor(100));
    expect(coverage(m.layers[0]!.items[0]!)).toBe('1.2k of 5.0k characters (24%)');
    expect(coverage(m.layers[1]!.items[0]!)).toBe('Binary');
    expect(sourceInfo(m.layers[0]!.items[0]!)).toEqual(expect.arrayContaining([['Stage', '1'], ['Artifact type', 'PRD']]));
  });
});
