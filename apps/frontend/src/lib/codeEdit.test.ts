import { describe, expect, it } from 'vitest';
import { applyEvent, classifyDiffLine, effectiveTargets, emptySession, fromSaved, pruneNested, startTurn, type EditEvent } from './codeEdit';

const run = (events: EditEvent[], prompt = 'fix it') => events.reduce(applyEvent, startTurn(emptySession(), prompt));

describe('conversation from streamed events', () => {
  it('builds the feed: messages, a tool step that finishes in place, and the summary', () => {
    const s = run([
      { type: 'session', id: 'S1', scope: 'uploaded', targets: ['a.py'] },
      { type: 'message', text: 'Reading a.py' },
      { type: 'tool', id: '1.1', tool: 'read_file', label: 'Reading a.py', status: 'running' },
      { type: 'tool', id: '1.1', tool: 'read_file', label: 'Read a.py', status: 'done' },
      { type: 'done', sessionId: 'S1', summary: 'All set', changes: [], usage: { model: 'm', promptTokens: 10, completionTokens: 5 } },
    ]);
    const items = s.turns[0]!.items;
    expect(items.map((i) => i.kind)).toEqual(['message', 'tool']);
    expect(items[1]).toMatchObject({ label: 'Read a.py', status: 'done' });
    expect(s).toMatchObject({ id: 'S1', model: 'm', tokens: 15 });
    expect(s.turns[0]).toMatchObject({ summary: 'All set', running: false });
  });

  it('keeps one entry per changed file while streaming, then takes the final set', () => {
    const ch = (added: number) => ({ type: 'change' as const, path: 'a.py', action: 'modify' as const, added, removed: 0, diff: '+x' });
    let s = run([ch(1), ch(2)]);
    expect(s.changes).toHaveLength(1);
    expect(s.changes[0]!.added).toBe(2);
    s = applyEvent(s, { type: 'done', sessionId: 'S', summary: 's', changes: [{ path: 'b.py', action: 'create', added: 3, removed: 0, diff: '' }] });
    expect(s.changes.map((c) => c.path)).toEqual(['b.py']);
  });

  it('shows an error on the running turn and stops it', () => {
    const s = run([{ type: 'error', code: 'FORBIDDEN', message: 'No write access' }]);
    expect(s.turns[0]).toMatchObject({ error: 'No write access', running: false });
  });

  it('a follow-up adds a turn and keeps the pending changes', () => {
    let s = run([{ type: 'done', sessionId: 'S', summary: 'one', changes: [{ path: 'a.py', action: 'modify', added: 1, removed: 0, diff: '' }] }]);
    s = startTurn(s, 'also tests');
    expect(s.turns).toHaveLength(2);
    expect(s.changes).toHaveLength(1);
    expect(s.turns[1]!.running).toBe(true);
  });
});

describe('saved sessions', () => {
  it('rebuilds turns from stored messages', () => {
    const s = fromSaved({ id: 'S', status: 'applied', targets: [], changes: [], applied: [{ path: 'a.py', action: 'modify', added: 1, removed: 1, diff: '' }],
      messages: [{ role: 'user', text: 'do it' }, { role: 'agent', text: 'done', steps: [{ tool: 'edit_file', label: 'Edited a.py', ok: true }, { tool: 'read_file', label: 'Read x', ok: false }] }] });
    expect(s.turns[0]).toMatchObject({ prompt: 'do it', summary: 'done' });
    expect(s.turns[0]!.items.map((i) => (i.kind === 'tool' ? i.status : ''))).toEqual(['done', 'error']);
    expect(s.applied).toHaveLength(1);
  });
});

describe('targets', () => {
  it('uses ticked paths, else the open file, else nothing; whole means no limit', () => {
    expect(effectiveTargets(['src', 'a.py'], 'b.py', false)).toEqual(['src', 'a.py']);
    expect(effectiveTargets([], 'b.py', false)).toEqual(['b.py']);
    expect(effectiveTargets([], null, false)).toEqual([]);
    expect(effectiveTargets(['a.py'], 'b.py', true)).toEqual([]);
  });
  it('a folder swallows the files ticked inside it', () => {
    expect(pruneNested(['src', 'src/a.py', 'lib/b.py', 'srcx/c.py'])).toEqual(['src', 'lib/b.py', 'srcx/c.py']);
  });
});

describe('diff lines', () => {
  it('classes additions, removals, hunks and headers', () => {
    expect(['+++ b/x', '--- a/x', '@@ -1 +1 @@', '+a', '-b', ' c'].map(classifyDiffLine)).toEqual(['meta', 'meta', 'hunk', 'add', 'del', 'ctx']);
  });
});
