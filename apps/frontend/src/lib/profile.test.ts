import { describe, expect, it } from 'vitest';
import { adviceIds, adviceItems, filterPacks, packButton, packState, stateText, unconfirmed, type PackView, type Recommendations, type Recommended } from './profile';

const pack = (o: Partial<PackView>): PackView => ({ id: 'p', name: 'Pack', description: '', kind: 'practice', kindLabel: 'Practice', version: 1, baseline: false, tags: { industries: [], regulations: [], domains: [] }, includes: [], count: 10, alreadyHave: 0, source: 'builtin', stages: [], ...o });
const rec = (o: Partial<Recommended>): Recommended => ({ id: 'r', name: 'R', kind: 'bundle', description: '', version: 1, score: 5, reasons: [], total: 10, have: 0, missing: 10, includes: [], ...o });
const R = (o: Partial<Recommendations>): Recommendations => ({ canAuthor: true, stage: 2, due: true, dismissed: false, profile: { items: [], text: '', empty: false, unconfirmed: 0 }, primary: null, bundles: [], packs: [], baseline: [], todo: [], ...o });

describe('profile words', () => {
  it('says where a value came from', () => {
    expect(stateText({ state: 'identified', sourceStage: 2 })).toBe('Found in stage 2');
    expect(stateText({ state: 'pinned', sourceStage: null })).toBe('Set by you');
    expect(stateText({ state: 'inherited', sourceStage: null })).toBe('From the organisation');
  });
  it('lists what still needs confirming', () => {
    const base = { kind: 'regulation' as const, label: 'GDPR', origin: 'project' as const, evidence: '', sourceStage: 2 };
    expect(unconfirmed([{ ...base, id: 'regulation:gdpr', value: 'gdpr', state: 'identified' }, { ...base, id: 'regulation:sox', value: 'sox', state: 'pinned' }])).toEqual(['regulation:gdpr']);
  });
});

describe('packs', () => {
  const packs = [pack({ id: 'a', name: 'PCI DSS', kind: 'regulation', tags: { industries: [], regulations: ['pci-dss'], domains: ['payments'] } }), pack({ id: 'b', name: 'Banking', kind: 'industry', tags: { industries: ['banking'], regulations: [], domains: [] } }), pack({ id: 'c', name: 'Bank EU', kind: 'bundle' })];
  it('filters by kind and searches names and tags', () => {
    expect(filterPacks(packs, 'bundle', '').map((p) => p.id)).toEqual(['c']);
    expect(filterPacks(packs, 'all', 'payments').map((p) => p.id)).toEqual(['a']);
    expect(filterPacks(packs, 'all', 'bank').map((p) => p.id)).toEqual(['b', 'c']);
  });
  it('knows what is added and what is left', () => {
    expect(packState(pack({ alreadyHave: 10 }))).toBe('applied');
    expect(packState(pack({ alreadyHave: 3 }))).toBe('partial');
    expect(packButton(pack({ alreadyHave: 3 }))).toBe('Add the other 7');
    expect(packButton(pack({ alreadyHave: 0 }))).toBe('Add 10 rules');
    expect(packButton(pack({ alreadyHave: 10 }))).toBe('Added');
  });
});

describe('advice', () => {
  it('offers the best ready-made set and the matching packs, never what is complete', () => {
    const r = R({ primary: rec({ id: 'set' }), packs: [rec({ id: 'x', kind: 'regulation' }), rec({ id: 'done', missing: 0, have: 10 })] });
    expect(adviceItems(r).map((x) => x.id)).toEqual(['set', 'x']);
    expect(adviceIds(r)).toEqual(['set']);
  });
  it('falls back to the essentials when there is no match', () => {
    const r = R({ baseline: [rec({ id: 'essentials' })] });
    expect(adviceIds(r)).toEqual(['essentials']);
  });
  it('is empty when everything is in place', () => {
    expect(adviceItems(R({ primary: rec({ missing: 0 }), baseline: [rec({ missing: 0 })] }))).toEqual([]);
  });
});
