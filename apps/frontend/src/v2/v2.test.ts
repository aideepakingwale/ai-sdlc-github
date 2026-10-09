import { describe, expect, it } from 'vitest';
import { auditCategory, auditCsv, buildPathTree } from './projectPanelLib';
import { resolveUiVersion } from './uiVersion';

describe('resolveUiVersion', () => {
  it('defaults to the new workspace', () => expect(resolveUiVersion('', null)).toBe('v2'));
  it('honours the query over storage', () => {
    expect(resolveUiVersion('?ui=v2', 'classic')).toBe('v2');
    expect(resolveUiVersion('?ui=classic', 'v2')).toBe('classic');
  });
  it('uses a remembered classic choice, and ignores anything else', () => {
    expect(resolveUiVersion('', 'classic')).toBe('classic');
    expect(resolveUiVersion('', 'v2')).toBe('v2');
    expect(resolveUiVersion('', 'junk')).toBe('v2');
  });
});

describe('project panel helpers', () => {
  it('categorises audit events', () => {
    expect(auditCategory({ event: 'security.review', humanReviewer: null })).toBe('Security');
    expect(auditCategory({ event: 'gate.approved', humanReviewer: 'a' })).toBe('Gate');
    expect(auditCategory({ event: 'comment', humanReviewer: 'a' })).toBe('Human');
  });
  it('escapes csv cells', () => {
    const csv = auditCsv([{ id: '1', timestamp: 't', phase: 1, agentRole: 'PO', event: 'a,"b"', provider: null, model: null, promptTokens: null, completionTokens: null, artefactHash: null, humanReviewer: null, detail: {} } as never]);
    expect(csv.split('\n')[1]).toContain('"a,""b"""');
  });
  it('builds a sorted tree', () => {
    const t = buildPathTree(['b.txt', 'src/a.ts', 'src/lib/c.ts']);
    expect(t.children?.map((c) => c.name)).toEqual(['src', 'b.txt']);
    expect(t.children?.[0].children?.map((c) => c.name)).toEqual(['lib', 'a.ts']);
  });
});

import { attentionItems, groupByLevel } from './pipelineLib';
const st = (phase: number, status: string, extra: object = {}) => ({ phase, name: `S${phase}`, status, reviewerRole: 'SA', ...extra }) as never;
describe('pipeline helpers', () => {
  it('orders attention by urgency', () => {
    const a = attentionItems([st(1, 'PENDING_REVIEW'), st(2, 'ESCALATED'), st(3, 'APPROVED', { stale: true })]);
    expect(a.map((x) => x.kind)).toEqual(['escalated', 'review', 'stale']);
  });
  it('groups parallel stages by level and falls back to one per step', () => {
    const stages = [st(1, 'A'), st(2, 'A'), st(3, 'A')];
    expect(groupByLevel(stages, [[1], [2, 3]]).map((g) => g.length)).toEqual([1, 2]);
    expect(groupByLevel(stages, undefined).length).toBe(3);
  });
});

import { resolveTheme } from './theme';
describe('theme', () => {
  it('follows the system only when asked', () => {
    expect(resolveTheme('system', true)).toBe('dark');
    expect(resolveTheme('system', false)).toBe('light');
    expect(resolveTheme('light', true)).toBe('light');
    expect(resolveTheme('dark', false)).toBe('dark');
  });
});

import { isNarrow, NARROW_PX } from './narrow';
describe('narrow layout', () => {
  it('switches at the breakpoint', () => {
    expect(isNarrow(NARROW_PX - 1)).toBe(true);
    expect(isNarrow(NARROW_PX)).toBe(false);
  });
});

import { languageMix } from './projectPanelLib';
describe('languageMix', () => {
  it('shares sum to 100 and fold the tail into Other', () => {
    const m = languageMix(['a.java', 'b.java', 'c.java', 'd.xml', 'e.yml', 'f.txt']);
    expect(m.reduce((s, x) => s + x.pct, 0)).toBe(100);
    expect(m[0]).toEqual({ name: 'Java', pct: 50 });
    expect(m[m.length - 1]!.name).toBe('Other');
  });
  it('is empty without files', () => expect(languageMix([])).toEqual([]));
});

import { portfolioStatus, portfolioTotals, type PortfolioProject } from './PortfolioView';
describe('portfolio table', () => {
  const p = (o: Partial<PortfolioProject>) => ({ stages: [{ phase: 1, name: 'a', color: 'slate', status: 'NOT_STARTED' }], approved: 0, awaitingReview: false, escalated: false, ...o }) as PortfolioProject;
  it('names the state of a project, most urgent first', () => {
    expect(portfolioStatus(p({ escalated: true, awaitingReview: true }))).toBe('Escalated');
    expect(portfolioStatus(p({ awaitingReview: true }))).toBe('Awaiting review');
    expect(portfolioStatus(p({ approved: 1 }))).toBe('Completed');
    expect(portfolioStatus(p({ stages: [{ phase: 1, name: 'a', color: 'blue', status: 'IN_PROGRESS' }] }))).toBe('In progress');
    expect(portfolioStatus(p({}))).toBe('Not started');
  });
  it('adds up the tiles', () => {
    expect(portfolioTotals([p({ approved: 2, awaitingReview: true }), p({ approved: 1, escalated: true })])).toEqual({ projects: 2, awaiting: 1, escalated: 1, approved: 3 });
  });
});

import { autoWidth } from './SplitPair';
describe('split pair width', () => {
  it('is a share of the container, never below the default, and capped', () => {
    expect(autoWidth(600, 300, 160, 900)).toBe(300);
    expect(autoWidth(1500, 300, 160, 900)).toBe(570);
    expect(autoWidth(4000, 300, 160, 900)).toBe(900);
  });
});
