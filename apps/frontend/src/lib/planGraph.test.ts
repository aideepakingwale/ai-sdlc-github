import { describe, expect, it } from 'vitest';
import { layout, neighbours, NODE_H, NODE_W, progress, stepLine, steps, summarise, type PlanGraph, type PlanNode } from './planGraph';

const n = (id: string, type: PlanNode['type'], layer: number, over: Partial<PlanNode> = {}): PlanNode => ({ id, type, label: id, sub: '', status: 'planned', layer, info: [], ...over });
const g: PlanGraph = {
  version: 1, stage: { seq: 2, key: 'hld', name: 'HLD', template: 2, persona: 'Architect', reviewerRole: 'SA' },
  nodes: [n('in', 'input', 0), n('guard', 'check', 1), n('lead', 'agent', 2), n('a', 'subagent', 3), n('b', 'subagent', 3), n('c', 'subagent', 3), n('sk', 'skill', 3),
    n('art1', 'artefact', 4), n('art2', 'artefact', 4), n('gate', 'gate', 5)],
  edges: [['in', 'guard'], ['guard', 'lead'], ['lead', 'a'], ['lead', 'b'], ['lead', 'c'], ['lead', 'sk'], ['a', 'art1'], ['b', 'art2'], ['c', 'art2'], ['art1', 'gate'], ['art2', 'gate']]
    .map(([from, to]) => ({ id: `${from}>${to}`, from: from!, to: to!, kind: 'flow' as const, label: '' })),
  layers: 6, counts: { agents: 4, subAgents: 3, skills: 1, tools: 0, artefacts: 2 }, run: { state: 'planned', jobId: null },
};

describe('layout', () => {
  const l = layout(g);
  it('puts each layer in its own column, left to right, and no two nodes overlap', () => {
    const x = (id: string) => l.nodes.find((p) => p.id === id)!.x;
    expect(x('in')).toBeLessThan(x('guard'));
    expect(x('lead')).toBeLessThan(x('a'));
    expect(x('a')).toBe(x('b'));
    expect(x('art1')).toBeLessThan(x('gate'));
    for (const p of l.nodes) for (const q of l.nodes) {
      if (p.id >= q.id) continue;
      const apart = p.x + NODE_W <= q.x || q.x + NODE_W <= p.x || p.y + NODE_H <= q.y || q.y + NODE_H <= p.y;
      expect(apart, `${p.id} overlaps ${q.id}`).toBe(true);
    }
    expect(l.width).toBeGreaterThan(5 * NODE_W);
    expect(l.nodes.every((p) => p.x >= 0 && p.y >= 0 && p.x + NODE_W <= l.width && p.y + NODE_H <= l.height)).toBe(true);
  });
  it('draws every edge from the right of its source to the left of its target', () => {
    expect(l.edges).toHaveLength(g.edges.length);
    const e = l.edges.find((x) => x.id === 'lead>a')!;
    const lead = l.nodes.find((p) => p.id === 'lead')!, a = l.nodes.find((p) => p.id === 'a')!;
    expect(e.d.startsWith(`M${lead.x + NODE_W},${lead.y + NODE_H / 2}`)).toBe(true);
    expect(e.d.endsWith(`${a.x},${a.y + NODE_H / 2}`)).toBe(true);
  });
  it('is deterministic, so a live update does not shuffle the picture', () => {
    expect(layout(g)).toEqual(layout(JSON.parse(JSON.stringify(g))));
  });
  it('copes with an empty graph and an edge to a missing node', () => {
    expect(layout({ nodes: [], edges: [] }).nodes).toEqual([]);
    expect(layout({ nodes: [n('x', 'input', 0)], edges: [{ id: 'x>y', from: 'x', to: 'y', kind: 'flow', label: '' }] }).edges).toEqual([]);
  });
});

describe('helpers', () => {
  it('finds a node and everything connected to it', () => {
    const f = neighbours(g, 'art2');
    expect([...f.nodes].sort()).toEqual(['art2', 'b', 'c', 'gate']);
    expect(f.edges.size).toBe(3);
  });
  it('says in one plain sentence what the graph shows', () => {
    expect(summarise(g)).toBe('Architect leads 3 sub-agents, with 1 skill, writing 2 deliverables for SA to sign off.');
    expect(summarise({ ...g, counts: { ...g.counts, subAgents: 1, tools: 2 }, stage: { ...g.stage, persona: null, reviewerRole: null } }))
      .toBe('The stage agent leads 1 sub-agent, with 1 skill, 2 tools, writing 2 deliverables for the reviewer to sign off.');
  });
  it('counts progress over the work nodes only, leaving out inputs and tools that are not connected', () => {
    const run: PlanGraph = { ...g, nodes: g.nodes.map((x) => (x.id === 'a' ? { ...x, status: 'done' } : x.id === 'b' ? { ...x, status: 'failed' } : x.id === 'sk' ? { ...x, unavailable: true } : x)) };
    expect(progress(run)).toEqual({ done: 1, total: 8, failed: 1, retrying: 0 });
  });
  it('lists the steps in the order they happen and says where each one is, including retries', () => {
    const run: PlanGraph = { ...g, nodes: g.nodes.map((x) => (x.id === 'a' ? { ...x, status: 'retrying', retries: 2, detail: '1 issue(s) found: reworking' } : x.id === 'b' ? { ...x, status: 'failed', detail: 'model said no' } : x.id === 'c' ? { ...x, status: 'done', retries: 1 } : x)) };
    const list = steps(run);
    expect(list.map((x) => x.id)).toEqual(['guard', 'lead', 'a', 'b', 'c', 'sk', 'art1', 'art2', 'gate']);          // inputs are not steps
    const by = (id: string) => stepLine(list.find((x) => x.id === id)!);
    expect(by('a')).toBe('Retrying · retried 2 times · 1 issue(s) found: reworking');
    expect(by('b')).toBe('Failed · model said no');
    expect(by('c')).toBe('Done · retried 1 time');
    expect(by('lead')).toBe('Planned');
    expect(progress(run)).toMatchObject({ retrying: 1, failed: 1 });
  });
});
