import { beforeEach, describe, expect, it } from 'vitest';
import { useApp } from './store';

const push = (e: Parameters<ReturnType<typeof useApp.getState>['pushEvent']>[0]) => useApp.getState().pushEvent(e);

describe('live generation parts', () => {
  beforeEach(() => useApp.getState().beginStream());

  it('keeps parallel artifacts as separate tabs instead of overwriting one document', () => {
    push({ type: 'part', part: 'hld', status: 'running' });
    push({ type: 'part', part: 'adr', status: 'running' });
    push({ type: 'content_start', part: 'document', title: 'Doc' });
    push({ type: 'content_delta', part: 'document', text: 'Hello ' });
    push({ type: 'content_delta', part: 'document', text: 'world' });
    push({ type: 'part', part: 'hld', status: 'done', text: '{"a":1}' });
    push({ type: 'part', part: 'adr', status: 'failed', error: 'boom' });
    const parts = useApp.getState().liveParts;
    expect(parts.map((p) => p.field)).toEqual(['hld', 'adr', 'document']);
    expect(parts.find((p) => p.field === 'document')?.text).toBe('Hello world');
    expect(parts.find((p) => p.field === 'hld')).toMatchObject({ status: 'done', text: '{"a":1}' });
    expect(parts.find((p) => p.field === 'adr')).toMatchObject({ status: 'failed', error: 'boom' });
  });

  it('rebuilds tabs from a replayed (coalesced) stream after reconnecting', () => {
    push({ type: 'content_start', title: 'Doc' });
    push({ type: 'content_delta', text: 'whole text so far' });
    expect(useApp.getState().liveParts).toEqual([
      { field: 'document', title: 'Doc', text: 'whole text so far', status: 'running' },
    ]);
  });
});
