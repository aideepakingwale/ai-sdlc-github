import { describe, expect, it } from 'vitest';
import { orderParts } from './partOrder';
import { isNearBottom } from './scroll';

const P = (field: string, status: 'running' | 'done' | 'failed') => ({ field, status });

describe('orderParts', () => {
  it('puts active generation first, failed next and completed last, keeping arrival order inside a group', () => {
    const out = orderParts([P('a', 'done'), P('b', 'running'), P('c', 'done'), P('d', 'failed'), P('e', 'running')]);
    expect(out.map((p) => p.field)).toEqual(['b', 'e', 'd', 'a', 'c']);
  });

  it('does not mutate its input', () => {
    const input = [P('a', 'done'), P('b', 'running')];
    orderParts(input);
    expect(input.map((p) => p.field)).toEqual(['a', 'b']);
  });
});

describe('isNearBottom', () => {
  it('only follows output when the user is within the threshold of the bottom', () => {
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 900, clientHeight: 100 })).toBe(true);
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 840, clientHeight: 100 })).toBe(true);   // 60px away
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 400, clientHeight: 100 })).toBe(false);  // reading higher up
  });
});
