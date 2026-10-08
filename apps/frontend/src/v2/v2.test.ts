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
