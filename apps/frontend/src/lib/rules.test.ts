import { describe, expect, it } from 'vitest';
import { checkWord, filterRules, hasHints, NO_FILTER, originLabel, tokens, usedBy, type Rule } from './rules';

const r = (over: Partial<Rule>): Rule => ({ id: 'x', category: 'rule', priority: 'should', stage: null, title: 'T', body: 'b', active: true, scope: 'project', createdAt: '', ...over });
const RULES = [r({ id: 'a', title: 'Encrypt data', body: 'at rest', priority: 'must' }), r({ id: 'b', title: 'Name things well', priority: 'context', category: 'preference' }),
  r({ id: 'c', title: 'Trace tests', stage: 4, priority: 'must' }), r({ id: 'd', title: 'Pipeline gates', stage: 5, priority: 'should' })];

describe('filterRules', () => {
  it('orders the most binding first, then by title', () => {
    expect(filterRules(RULES, NO_FILTER).map((x) => x.id)).toEqual(['a', 'c', 'd', 'b']);
  });
  it('searches title and body', () => {
    expect(filterRules(RULES, { ...NO_FILTER, query: 'AT REST' }).map((x) => x.id)).toEqual(['a']);
  });
  it('a stage shows its own rules and the ones for every stage; "every" shows only the latter', () => {
    expect(filterRules(RULES, { ...NO_FILTER, stage: 4 }).map((x) => x.id)).toEqual(['a', 'c', 'b']);
    expect(filterRules(RULES, { ...NO_FILTER, stage: 'every' }).map((x) => x.id)).toEqual(['a', 'b']);
  });
  it('narrows by priority and category', () => {
    expect(filterRules(RULES, { ...NO_FILTER, priority: 'must' }).map((x) => x.id)).toEqual(['a', 'c']);
    expect(filterRules(RULES, { ...NO_FILTER, category: 'preference' }).map((x) => x.id)).toEqual(['b']);
  });
});

describe('words', () => {
  it('describes a check', () => {
    expect(checkWord(undefined)).toBeNull();
    expect(checkWord({ complied: 2, violated: 0, unclear: 0 })).toEqual({ text: 'Followed in 2 stages', tone: 'green' });
    expect(checkWord({ complied: 1, violated: 1, unclear: 0 })).toEqual({ text: 'Not followed in 1 stage', tone: 'red' });
    expect(checkWord({ complied: 0, violated: 0, unclear: 1 })?.tone).toBe('slate');
  });
  it('names where a rule came from', () => {
    expect(originLabel('pack:security-baseline')).toBe('From a starter pack');
    expect(originLabel('memory:m1')).toBe('From team memory');
    expect(originLabel(null)).toBe('');
  });
  it('knows when there is anything to warn about', () => {
    expect(hasHints(null)).toBe(false);
    expect(hasHints({ duplicates: [], conflicts: [], stack: [], ok: true })).toBe(false);
    expect(hasHints({ duplicates: [{ id: '1', title: 't', scope: 'project' }], conflicts: [], stack: [], ok: false })).toBe(true);
  });
  it('estimates tokens and says where a template is used', () => {
    expect(tokens(850)).toBe('850');
    expect(tokens(2400)).toBe('2.4k');
    expect(usedBy({ usage: { count: 2, phases: [2, 3] } })).toBe('2 artefacts in stage 2, 3');
    expect(usedBy({ usage: { count: 0, phases: [] } })).toBe('Not used yet');
  });
});
