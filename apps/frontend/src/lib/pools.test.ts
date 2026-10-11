import { describe, expect, it } from 'vitest';
import { isBlocked, balanceLine, orgNote, parseTokens, signedTokens, sortPools, usedShare, type PoolState, type ProjectPool } from './pools';

describe('token pool helpers', () => {
  it('reads the ways people write a token amount', () => {
    expect(['1.5M', '250k', '1,200,000', '300000', ' 2 m ', '1b', '0.5k'].map(parseTokens)).toEqual([1_500_000, 250_000, 1_200_000, 300_000, 2_000_000, 1_000_000_000, 500]);
    expect(['', 'abc', '1.2.3', '10x', 'k'].map(parseTokens)).toEqual([null, null, null, null, null]);
    expect(parseTokens('99999999999999999999')).toBeNull();
  });

  it('shows a negative balance with a proper minus', () => {
    expect([signedTokens(1500), signedTokens(-12000), signedTokens(0)]).toEqual(['1,500', '−12k', '0']);
  });

  it('the share used can pass one when overdrawn, and is zero with nothing allocated or used', () => {
    expect(usedShare({ allocated: 1000, consumed: 250 })).toBe(0.25);
    expect(usedShare({ allocated: 1000, consumed: 1800 })).toBe(1.8);
    expect(usedShare({ allocated: 0, consumed: 0 })).toBe(0);
  });

  it('the balance line says what is left, for how long, or that it is overdrawn', () => {
    const base = { metered: true, daysLeft: null, runsOutOn: null, state: 'ok' as const };
    expect(balanceLine({ ...base, metered: false, balance: null })).toMatch(/not limited/);
    expect(balanceLine({ ...base, balance: 412_000, daysLeft: 9.4 })).toBe('412k tokens left · about 9 days at this pace');
    expect(balanceLine({ ...base, balance: 5000, daysLeft: 0.4 })).toBe('5,000 tokens left · about a day at this pace');
    expect(balanceLine({ ...base, balance: 5000 })).toBe('5,000 tokens left');
    expect(balanceLine({ ...base, balance: 900_000, daysLeft: 229 })).toBe('900k tokens left · more than 3 months at this pace');
    expect(balanceLine({ ...base, balance: 0 })).toBe('No tokens left');
    expect(balanceLine({ ...base, balance: -3000 })).toBe('Overdrawn by 3,000 tokens');
  });

  it('only mentions the organisation when its tokens are the problem', () => {
    const p = (state: string, metered = true) => ({ organisation: { metered, balance: 1, state, level: 0 } }) as unknown as ProjectPool;
    expect([orgNote(p('ok')), orgNote(p('unmetered', false)), orgNote(p('low')), orgNote(p('empty')), orgNote(p('overdrawn'))]).toEqual([null, null, 'The organisation’s tokens are running low.', 'The organisation’s tokens are used up.', 'The organisation’s tokens are used up.']);
  });
});

describe('sortPools', () => {
  it('puts the ones that need attention first, then healthy, then unmetered, busiest first', () => {
    const r = (name: string, state: PoolState, used30d: number) => ({ name, state, used30d });
    const out = sortPools([r('c', 'unmetered', 50), r('a', 'ok', 5), r('b', 'low', 1), r('d', 'overdrawn', 0), r('e', 'unmetered', 90), r('f', 'ok', 9)]);
    expect(out.map((x) => x.name)).toEqual(['d', 'b', 'f', 'a', 'e', 'c']);
  });
});

describe('isBlocked', () => {
  it('is true when the project or the organisation is used up, false otherwise', () => {
    const p = (a: string, b: string) => ({ state: a, organisation: { state: b } }) as unknown as ProjectPool;
    expect([isBlocked(p('ok', 'ok')), isBlocked(p('low', 'unmetered')), isBlocked(p('empty', 'ok')), isBlocked(p('overdrawn', 'ok')), isBlocked(p('ok', 'empty')), isBlocked(p('unmetered', 'overdrawn'))])
      .toEqual([false, false, true, true, true, true]);
  });
});
