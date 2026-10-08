import type { AuditEvent } from '../api/types';
import type { CodeNode } from '../lib/codeTree';

export const AUDIT_CATEGORIES = ['All', 'Gate', 'Generation', 'Security', 'Guardrail', 'Human'] as const;
export type AuditCategory = (typeof AUDIT_CATEGORIES)[number];

/** Which filter chip an audit event belongs under. */
export function auditCategory(e: Pick<AuditEvent, 'event' | 'humanReviewer'>): Exclude<AuditCategory, 'All'> {
  const ev = e.event;
  if (ev.startsWith('security.')) return 'Security';
  if (ev.startsWith('guardrail.')) return 'Guardrail';
  if (ev.startsWith('gate.')) return 'Gate';
  if (ev.startsWith('ai.') || ev.startsWith('stage.') || ev.startsWith('build.')) return 'Generation';
  return e.humanReviewer ? 'Human' : 'Generation';
}

const csvCell = (v: unknown): string => {
  const s = v == null ? '' : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

export function auditCsv(events: AuditEvent[]): string {
  const head = ['time', 'event', 'stage', 'agentRole', 'provider', 'model', 'promptTokens', 'completionTokens', 'humanReviewer', 'artefactHash'];
  const rows = events.map((e) => [e.timestamp, e.event, e.phase, e.agentRole, e.provider, e.model, e.promptTokens, e.completionTokens, e.humanReviewer, e.artefactHash].map(csvCell).join(','));
  return [head.join(','), ...rows].join('\n');
}

/** A directory tree from flat file paths (the uploaded codebase lists paths only). */
export function buildPathTree(paths: string[]): CodeNode {
  const root: CodeNode = { name: '', path: '', type: 'dir', purpose: '', children: [] };
  for (const full of [...paths].sort()) {
    const parts = full.split('/').filter(Boolean);
    let node = root;
    parts.forEach((part, i) => {
      const path = parts.slice(0, i + 1).join('/');
      const last = i === parts.length - 1;
      node.children ??= [];
      let next = node.children.find((c) => c.path === path);
      if (!next) { next = { name: part, path, type: last ? 'file' : 'dir', purpose: '', ...(last ? {} : { children: [] }) }; node.children.push(next); }
      node = next;
    });
  }
  const order = (n: CodeNode): void => {
    n.children?.sort((a, b) => (a.type === b.type ? a.name.localeCompare(b.name) : a.type === 'dir' ? -1 : 1));
    n.children?.forEach(order);
  };
  order(root);
  return root;
}
