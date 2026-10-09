import { describe, expect, it } from 'vitest';
import { isDirty, statusOf, type Connection } from './connections';

const c = (o: Partial<Connection>): Connection => ({ settings: {}, secretSet: false, lastTest: null, updatedBy: null, updatedAt: null, ...o });
const t = (ok: boolean) => ({ ok, at: '', by: '', checks: [], usesOwnCredential: true });

describe('connection status', () => {
  it('reads the last test first', () => {
    expect(statusOf('github', c({ settings: { repo: 'a/b' }, secretSet: true, lastTest: t(true) })).label).toBe('Connected');
    expect(statusOf('jira', c({ lastTest: t(false) })).tone).toBe('red');
  });
  it('tells apart not set up, shared and untested', () => {
    expect(statusOf('github', c({})).label).toBe('Not set up');
    expect(statusOf('github', c({ settings: { repo: 'a/b' } })).label).toBe('Using the shared connection');
    expect(statusOf('jira', c({ settings: { projectKey: 'PAY' }, secretSet: true })).label).toBe('Not tested yet');
    expect(statusOf('kb', c({})).label).toBe('Default');
  });
  it('knows when the form changed', () => {
    const saved = c({ settings: { repo: 'a/b' } });
    expect(isDirty(saved, { repo: 'a/b' }, '')).toBe(false);
    expect(isDirty(saved, { repo: 'a/c' }, '')).toBe(true);
    expect(isDirty(saved, { repo: 'a/b' }, 'tok')).toBe(true);
  });
});
