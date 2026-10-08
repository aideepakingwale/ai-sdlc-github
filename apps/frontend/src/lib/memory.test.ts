import { describe, expect, it } from 'vitest';
import { filterMemories, groupMemories, sourceLabel, type MemoryEntry } from './memory';

const m = (o: Partial<MemoryEntry>): MemoryEntry => ({
  id: 'a', scope: 'project', projectId: 'p', kind: 'decision', title: 'Use SQS', body: 'FIFO queues', stage: null,
  status: 'active', source: {}, uses: 0, lastUsedAt: null, createdBy: null, createdAt: '', updatedAt: '', ...o,
});

describe('memory helpers', () => {
  it('says where a memory came from', () => {
    expect(sourceLabel(m({ source: { type: 'clarification', stage: 'Solution Architecture' } }))).toBe('From an answer to a clarifying question in Solution Architecture');
    expect(sourceLabel(m({ source: { type: 'change_request', phase: 3 } }))).toBe('From a change request in stage 3');
    expect(sourceLabel(m({ source: { type: 'manual', by: 'a@b.io' } }))).toBe('Added by a@b.io');
    expect(sourceLabel(m({ source: {} }))).toBe('Added by hand');
  });
  it('filters by kind and text', () => {
    const list = [m({ id: '1' }), m({ id: '2', kind: 'lesson', title: 'Add retries', body: 'every design' })];
    expect(filterMemories(list, { kind: 'lesson' }).map((x) => x.id)).toEqual(['2']);
    expect(filterMemories(list, { query: 'FIFO' }).map((x) => x.id)).toEqual(['1']);
    expect(filterMemories(list, { kind: 'all', query: '' })).toHaveLength(2);
  });
  it('groups by status and leaves out rejected ones', () => {
    const g = groupMemories([m({ id: '1', status: 'suggested' }), m({ id: '2' }), m({ id: '3', status: 'archived' }), m({ id: '4', status: 'rejected' })]);
    expect([g.suggested.length, g.active.length, g.archived.length]).toEqual([1, 1, 1]);
  });
});
