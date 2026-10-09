/**
 * Pure model for the code assistant: the shapes the API streams, how a stream of events builds up one conversation,
 * and the small helpers the panel needs (which files a request targets, how a diff line is classed). No React, no DOM.
 */
export type EditScope = 'generated' | 'uploaded';
export type ChangeAction = 'modify' | 'create' | 'delete';
export interface EditChange { path: string; action: ChangeAction; added: number; removed: number; diff: string }
export interface ToolStep { id: string; tool: string; label: string; status: 'running' | 'done' | 'error'; detail?: string }
export type FeedItem = { kind: 'message'; text: string } | ({ kind: 'tool' } & ToolStep);
export interface Turn { prompt: string; items: FeedItem[]; summary: string; error: string; running: boolean }
export interface EditSession {
  id: string | null; turns: Turn[]; changes: EditChange[]; applied: EditChange[]; status: 'proposed' | 'applied' | 'reverted' | 'discarded';
  model?: string; tokens?: number;
}

export type EditEvent =
  | { type: 'session'; id: string; scope: string; targets: string[] }
  | { type: 'message'; text: string }
  | ({ type: 'tool' } & ToolStep)
  | ({ type: 'change' } & EditChange)
  | { type: 'done'; sessionId: string; summary: string; changes: EditChange[]; usage?: { model?: string; promptTokens?: number; completionTokens?: number } }
  | { type: 'error'; code: string; message: string };

export const emptySession = (): EditSession => ({ id: null, turns: [], changes: [], applied: [], status: 'proposed' });

export function startTurn(s: EditSession, prompt: string): EditSession {
  return { ...s, status: 'proposed', turns: [...s.turns, { prompt, items: [], summary: '', error: '', running: true }] };
}

const lastTurn = (s: EditSession, f: (t: Turn) => Turn): EditSession => {
  if (s.turns.length === 0) return s;
  const turns = s.turns.slice();
  turns[turns.length - 1] = f(turns[turns.length - 1]!);
  return { ...s, turns };
};

/** Fold one streamed event into the conversation. */
export function applyEvent(s: EditSession, e: EditEvent): EditSession {
  switch (e.type) {
    case 'session': return { ...s, id: e.id };
    case 'message': return lastTurn(s, (t) => ({ ...t, items: [...t.items, { kind: 'message', text: e.text }] }));
    case 'tool': {
      const { type: _t, ...step } = e;
      return lastTurn(s, (t) => {
        const i = t.items.findIndex((x) => x.kind === 'tool' && x.id === step.id);
        const item: FeedItem = { kind: 'tool', ...step };
        return { ...t, items: i < 0 ? [...t.items, item] : t.items.map((x, j) => (j === i ? item : x)) };
      });
    }
    case 'change': {
      const { type: _t, ...ch } = e;
      const rest = s.changes.filter((c) => c.path !== ch.path);
      return { ...s, changes: [...rest, ch] };
    }
    case 'done': {
      const tokens = (e.usage?.promptTokens ?? 0) + (e.usage?.completionTokens ?? 0);
      return { ...lastTurn(s, (t) => ({ ...t, summary: e.summary, running: false })), id: e.sessionId, changes: e.changes, model: e.usage?.model, tokens: (s.tokens ?? 0) + tokens };
    }
    case 'error': return lastTurn(s, (t) => ({ ...t, error: e.message, running: false }));
  }
}

/** A saved session (GET /code-edit/:id) back into the conversation shape. */
export interface SavedSession {
  id: string; status: EditSession['status']; targets: string[]; changes: EditChange[]; applied: EditChange[];
  messages: Array<{ role: 'user' | 'agent'; text: string; steps?: Array<{ tool: string; label: string; ok: boolean }> }>;
  usage?: { model?: string; promptTokens?: number; completionTokens?: number };
}
export function fromSaved(r: SavedSession): EditSession {
  const turns: Turn[] = [];
  for (const m of r.messages) {
    if (m.role === 'user') turns.push({ prompt: m.text, items: [], summary: '', error: '', running: false });
    else if (turns.length) {
      const t = turns[turns.length - 1]!;
      t.summary = m.text;
      t.items = (m.steps ?? []).map((st, i): FeedItem => ({ kind: 'tool', id: `s${turns.length}.${i}`, tool: st.tool, label: st.label, status: st.ok ? 'done' : 'error' }));
    }
  }
  return { id: r.id, turns, changes: r.changes, applied: r.applied, status: r.status, model: r.usage?.model, tokens: (r.usage?.promptTokens ?? 0) + (r.usage?.completionTokens ?? 0) };
}

/** The files and folders a request is limited to: what is ticked, else the open file. `whole` = the entire codebase. */
export function effectiveTargets(checked: string[], openFile: string | null, whole: boolean): string[] {
  if (whole) return [];
  if (checked.length) return pruneNested(checked);
  return openFile ? [openFile] : [];
}

/** Ticking a folder and a file inside it is the folder. */
export function pruneNested(paths: string[]): string[] {
  const uniq = [...new Set(paths)];
  return uniq.filter((p) => !uniq.some((q) => q !== p && p.startsWith(`${q.replace(/\/$/, '')}/`)));
}

export type DiffLineKind = 'add' | 'del' | 'hunk' | 'ctx' | 'meta';
export function classifyDiffLine(l: string): DiffLineKind {
  if (l.startsWith('+++') || l.startsWith('---')) return 'meta';
  if (l.startsWith('@@')) return 'hunk';
  if (l.startsWith('+')) return 'add';
  if (l.startsWith('-')) return 'del';
  return 'ctx';
}

export const ACTION_LABEL: Record<ChangeAction, string> = { modify: 'Modified', create: 'New file', delete: 'Deleted' };
