import { describe, expect, it } from 'vitest';
import { auditCategory, auditCsv, buildPathTree } from './projectPanelLib';
import { resolveUiVersion } from './uiVersion';

describe('resolveUiVersion', () => {
  it('defaults to classic', () => expect(resolveUiVersion('', null)).toBe('classic'));
  it('honours the query over storage', () => {
    expect(resolveUiVersion('?ui=v2', 'classic')).toBe('v2');
    expect(resolveUiVersion('?ui=classic', 'v2')).toBe('classic');
  });
  it('uses the remembered choice', () => expect(resolveUiVersion('', 'v2')).toBe('v2'));
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
