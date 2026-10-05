import { beforeEach, describe, expect, it } from 'vitest';
import { streamKey, useApp } from './store';
import { orderParts } from './lib/partOrder';

const KEY = streamKey('p1', 3);
const push = (e: Parameters<ReturnType<typeof useApp.getState>['pushEvent']>[1], key = KEY) => useApp.getState().pushEvent(key, e);
const parts = (key = KEY) => useApp.getState().streams[key]?.liveParts ?? [];

describe('live generation parts', () => {
  beforeEach(() => { useApp.setState({ streams: {} }); useApp.getState().beginStream(KEY); });

  it('keeps parallel artifacts as separate tabs instead of overwriting one document', () => {
    push({ type: 'part', part: 'hld', status: 'running' });
    push({ type: 'part', part: 'adr', status: 'running' });
    push({ type: 'content_start', part: 'document', title: 'Doc' });
    push({ type: 'content_delta', part: 'document', text: 'Hello ' });
    push({ type: 'content_delta', part: 'document', text: 'world' });
    push({ type: 'part', part: 'hld', status: 'done', text: '{"a":1}' });
    push({ type: 'part', part: 'adr', status: 'failed', error: 'boom' });
    const got = parts();
    expect(got.map((p) => p.field)).toEqual(['adr', 'document', 'hld']);  // finished goes last
    expect(got.find((p) => p.field === 'document')?.text).toBe('Hello world');
    expect(got.find((p) => p.field === 'hld')).toMatchObject({ status: 'done', text: '{"a":1}' });
    expect(got.find((p) => p.field === 'adr')).toMatchObject({ status: 'failed', error: 'boom' });
  });

  it('rebuilds tabs from a replayed (coalesced) stream after reconnecting', () => {
    push({ type: 'content_start', title: 'Doc' });
    push({ type: 'content_delta', text: 'whole text so far' });
    expect(parts()).toEqual([
      { field: 'document', title: 'Doc', text: 'whole text so far', status: 'running' },
    ]);
  });

  it('moves a part to the end when it completes (completion order), others keep their place', () => {
    push({ type: 'part', part: 'lld', status: 'running' });
    push({ type: 'part', part: 'api', status: 'running' });
    push({ type: 'part', part: 'db', status: 'running' });
    push({ type: 'part', part: 'api', status: 'done', text: 'x' });
    push({ type: 'part', part: 'lld', status: 'done', text: 'y' });
    expect(parts().map((p) => p.field)).toEqual(['db', 'api', 'lld']);
    // …and the tab bar then shows the one still writing first, finished ones after, in completion order
    expect(orderParts(parts()).map((p) => p.field)).toEqual(['db', 'api', 'lld']);
  });
});


describe('runs are independent', () => {
  beforeEach(() => useApp.setState({ streams: {} }));

  it('a run in one project or stage never appears in another', () => {
    const a = streamKey('projA', 3); const b = streamKey('projB', 3); const a2 = streamKey('projA', 2);
    useApp.getState().beginStream(a);
    push({ type: 'content_start', part: 'LLD', title: 'LLD' }, a);
    push({ type: 'content_delta', part: 'LLD', text: 'writing' }, a);
    const s = useApp.getState().streams;
    expect(s[a]).toMatchObject({ active: true });
    expect(s[b]).toBeUndefined();                       // the other project has no run: it is not "generating"
    expect(s[a2]).toBeUndefined();                      // …and neither has another stage of the same project
    expect(parts(a).map((p) => p.field)).toEqual(['LLD']);
  });

  it('two runs stream at once without overwriting each other, and each ends on its own', () => {
    const a = streamKey('projA', 3); const b = streamKey('projB', 3);
    useApp.getState().beginStream(a); useApp.getState().beginStream(b);
    push({ type: 'content_delta', part: 'x', text: 'A' }, a);
    push({ type: 'content_delta', part: 'x', text: 'B' }, b);
    useApp.getState().endStream(a);
    const s = useApp.getState().streams;
    expect([s[a]!.active, s[b]!.active]).toEqual([false, true]);
    expect([parts(a)[0]!.text, parts(b)[0]!.text]).toEqual(['A', 'B']);
  });

  it('keeps what was streamed after the run ends (the screen can still show it) but prunes old finished runs', () => {
    const first = streamKey('p', 1);
    useApp.getState().beginStream(first);
    push({ type: 'content_delta', part: 'x', text: 'kept' }, first);
    useApp.getState().endStream(first);
    expect(parts(first)[0]!.text).toBe('kept');
    for (let i = 2; i < 14; i++) { const k = streamKey('p', i); useApp.getState().beginStream(k); useApp.getState().endStream(k); }
    expect(Object.keys(useApp.getState().streams).length).toBeLessThanOrEqual(10);
  });

  it('learns the project id from the first chat turn of a new project', () => {
    useApp.setState({ activeProjectId: null });
    const k = streamKey(null, 'chat');
    useApp.getState().beginStream(k);
    push({ type: 'session', projectId: 'new1', sessionId: 's', phase: 1 } as never, k);
    expect(useApp.getState().activeProjectId).toBe('new1');
  });
});
