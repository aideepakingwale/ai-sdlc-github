import { describe, expect, it } from 'vitest';
import { coverage, layoutGraph, neighbours, radiusFor, sourceInfo, visibleItems, type ContextManifest, type ManifestItem } from './contextGraph';

const item = (id: string, layer: string, over: Partial<ManifestItem> = {}): ManifestItem => ({
  id, layer, label: id, kind: 'item', status: 'full', chars: 400, totalChars: 400, tokens: 100, source: {}, note: '', ...over });

const m: ContextManifest = {
  mode: 'preview', phase: 4, stage: 'Test Engineering', generatedAt: '',
  outputs: [{ id: 'output:HLD', type: 'HLD' }, { id: 'output:ADR', type: 'ADR' }],
  lineage: [
    { phase: 1, name: 'Requirements', dependsOn: [], direct: false },
    { phase: 2, name: 'Architecture', dependsOn: [1], direct: false },
    { phase: 3, name: 'Design', dependsOn: [1, 2], direct: true },
  ],
  layers: [
    { id: 'instructions', label: 'Fixed instructions', hint: '', chars: 0, tokens: 0, items: [item('i1', 'instructions')] },
    { id: 'canon', label: 'Standards & templates', hint: '', chars: 0, tokens: 0, items: [item('t1', 'canon', { kind: 'template', source: { type: 'formwork', artefactType: 'HLD' } })] },
    { id: 'upstream', label: 'Previous stages', hint: '', chars: 0, tokens: 0, items: [
      item('u1', 'upstream', { label: '[Stage 1] PRD', status: 'condensed', chars: 1200, totalChars: 5000, source: { phase: 1, artifactType: 'PRD' } }),
      item('u2', 'upstream', { label: '[Stage 2] HLD', source: { phase: 2, artifactType: 'HLD' } }),
      item('u3', 'upstream', { label: '[Stage 3] LLD', source: { phase: 3, artifactType: 'LLD' } })] },
    { id: 'input', label: 'Your instructions', hint: '', chars: 0, tokens: 0, items: [item('input:instruction', 'input')] },
    { id: 'attached', label: 'Attached', hint: '', chars: 0, tokens: 0, items: [item('a1', 'attached', { label: 'spec.docx', status: 'excluded', note: 'Binary' }), item('a2', 'attached')] },
    { id: 'revision', label: 'History', hint: '', chars: 0, tokens: 0, items: [item('r1', 'revision', { kind: 'revision', source: { type: 'revision', artifactType: 'ADR' } })] },
  ],
  edges: [{ id: 'u1->prompt', from: 'u1', to: 'prompt', label: 'builds on' }, { id: 'a2->output:HLD', from: 'a2', to: 'output:HLD', label: 'layout followed' }, { id: 'prompt->output:HLD', from: 'prompt', to: 'output:HLD', label: 'produces' }],
  totals: { chars: 0, tokens: 0, items: 8, condensed: 1, excluded: 1 },
};

const at = (g: ReturnType<typeof layoutGraph>, id: string) => g.nodes.find((n) => n.id === id)!;

describe('context graph', () => {
  it('is a layered hierarchy: fixed context, then the stages in dependency order, then the prompt, then the outputs', () => {
    const g = layoutGraph(m);
    const x = (id: string) => at(g, id).x;
    expect(x('group:stage:1')).toBeLessThan(x('group:stage:2'));                 // earlier stages sit to the left of the ones that build on them
    expect(x('group:stage:2')).toBeLessThan(x('group:stage:3'));
    expect(x('group:instructions')).toBeLessThan(x('group:stage:1'));            // fixed context is the first column
    expect(x('group:stage:3')).toBeLessThan(x('prompt'));
    expect(x('prompt')).toBeLessThan(x('output:HLD'));
    expect(at(g, 'u1').parent).toBe('group:stage:1');                            // items live inside their stage's box
    expect(g.nodes.filter((n) => n.kind === 'group').length).toBeGreaterThanOrEqual(8);
  });

  it('connects things to each other, not every item to one hub', () => {
    const g = layoutGraph(m);
    const has = (from: string, to: string, kind?: string) => g.edges.some((e) => e.from === from && e.to === to && (!kind || e.kind === kind));
    expect(has('group:stage:1', 'group:stage:2', 'depends')).toBe(true);         // stage depends on stage
    expect(has('group:stage:1', 'group:stage:3', 'depends')).toBe(true);
    expect(has('group:stage:3', 'prompt', 'feeds')).toBe(true);                  // only the stage it directly builds on feeds the prompt
    expect(has('group:stage:1', 'prompt')).toBe(false);
    expect(has('group:canon', 'prompt', 'feeds')).toBe(true);
    expect(has('t1', 'output:HLD', 'cross')).toBe(true);                         // a template an artifact follows
    expect(has('a2', 'output:HLD', 'cross')).toBe(true);                         // an attached file whose layout it follows
    expect(has('r1', 'output:ADR', 'cross')).toBe(true);                         // the previous version it revises
    expect(has('a2', 'input:instruction', 'cross')).toBe(true);                  // the files the instructions refer to
    expect(g.edges.filter((e) => e.to === 'prompt' && e.from.startsWith('group:')).length).toBeLessThanOrEqual(6);
    expect(g.edges.every((e) => e.d && e.d.startsWith('M'))).toBe(true);
  });

  it('keeps links that skip a column out of the way, running below the boxes', () => {
    const g = layoutGraph(m);
    const long = g.edges.find((e) => e.id === 'group:stage:1->group:stage:3')!;
    expect(long.ly!).toBeGreaterThan(Math.max(...g.nodes.filter((n) => n.kind === 'group').map((n) => n.y + (n.h ?? 0) / 2)));
  });

  it('is deterministic and keeps items apart', () => {
    const g1 = layoutGraph(m); const g2 = layoutGraph(m);
    expect(g1).toEqual(g2);
    const pts = g1.nodes.filter((n) => n.kind === 'item');
    for (let i = 0; i < pts.length; i++) for (let j = i + 1; j < pts.length; j++) {
      expect(Math.hypot(pts[i]!.x - pts[j]!.x, pts[i]!.y - pts[j]!.y)).toBeGreaterThan(pts[i]!.r + pts[j]!.r);
    }
  });

  it('falls back to one "Previous stages" box when the manifest has no lineage', () => {
    const g = layoutGraph({ ...m, lineage: undefined });
    expect(g.nodes.some((n) => n.id === 'group:upstream')).toBe(true);
    expect(g.nodes.some((n) => n.id.startsWith('group:stage:'))).toBe(false);
  });

  it('drops hidden items together with their links, and empty boxes with them', () => {
    const hidden = layoutGraph(m, { hideExcluded: true });
    expect(hidden.nodes.some((n) => n.id === 'a1')).toBe(false);
    expect(hidden.edges.every((e) => e.from !== 'a1' && e.to !== 'a1')).toBe(true);
    const only = layoutGraph(m, { layers: ['canon'] });
    expect(only.nodes.filter((n) => n.kind === 'group').map((n) => n.id)).toEqual(['group:canon']);
  });

  it('filters by layer and by search over label, note and source', () => {
    expect(visibleItems(m, { layers: ['attached'] }).map((i) => i.id)).toEqual(['a1', 'a2']);
    expect(visibleItems(m, { query: 'prd' }).map((i) => i.id)).toEqual(['u1']);
    expect(visibleItems(m, { query: 'binary' }).map((i) => i.id)).toEqual(['a1']);
    expect(visibleItems(m, { hideExcluded: true })).toHaveLength(8);
  });

  it('finds a node\'s relationships for highlighting, including the links of the box it sits in', () => {
    const g = layoutGraph(m);
    const n = neighbours(g, 'output:HLD');
    expect(n.nodes.has('prompt') && n.nodes.has('a2') && n.nodes.has('t1')).toBe(true);
    const item = neighbours(g, 'u1');
    expect(item.nodes.has('group:stage:1') && item.nodes.has('group:stage:2')).toBe(true);   // lights its stage's links
    expect(item.edges.has('group:stage:1->group:stage:2')).toBe(true);
    expect(neighbours(g, 'group:stage:1').nodes.has('u1')).toBe(true);                       // selecting a box keeps its items in view
  });

  it('sizes nodes by tokens within bounds and describes coverage and sources in plain words', () => {
    expect(radiusFor(0)).toBe(9); expect(radiusFor(10_000_000)).toBe(26);
    expect(radiusFor(4_000)).toBeGreaterThan(radiusFor(100));
    expect(coverage(m.layers[2]!.items[0]!)).toBe('1.2k of 5.0k characters (24%)');
    expect(coverage(m.layers[4]!.items[0]!)).toBe('Binary');
    expect(sourceInfo(m.layers[2]!.items[0]!)).toEqual(expect.arrayContaining([['Stage', '1'], ['Artifact type', 'PRD']]));
  });
});
