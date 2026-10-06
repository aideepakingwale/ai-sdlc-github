/**
 * Pure model for the code explorer of two-step code generation: types for what the API returns, tree
 * helpers (filter, expand, counts), the step tracker and file presentation. No React, no DOM.
 */
export type CodeStatus = 'none' | 'proposed' | 'approved' | 'implemented' | 'committed';
export interface CodeNode {
  name: string; path: string; type: 'dir' | 'file'; purpose: string;
  kind?: string; layer?: string; generated?: boolean; children?: CodeNode[];
}
export interface CodeFile { path: string; purpose: string; kind: string; layer: string; generated: boolean; artefactId: string | null; chars: number }
export interface CodeView {
  enabled: boolean; status: CodeStatus; checkpoint?: 'structure' | 'code' | null; version?: number;
  structure?: { summary: string; conventions: string[]; directories: Array<{ path: string; purpose: string }> };
  tree?: CodeNode; files?: CodeFile[]; branch?: string; commitMessage?: string; decidedBy?: string | null; decidedAt?: string | null;
  implementedAt?: string | null; committedAt?: string | null; commitRef?: string | null; generatedCount?: number; fileCount?: number; canReset?: boolean;
}

export interface Step { id: string; label: string; hint: string; state: 'done' | 'current' | 'todo' }

/** The four-step journey of the stage: propose → approve structure → write code → approve code & commit. */
export function steps(v: CodeView): Step[] {
  const s = v.status;
  const rank = { none: 0, proposed: 1, approved: 2, implemented: 3, committed: 4 }[s] ?? 0;
  const mk = (id: string, label: string, hint: string, doneAt: number, currentAt: number): Step => ({
    id, label, hint, state: rank >= doneAt ? 'done' : rank === currentAt ? 'current' : 'todo' });
  return [
    mk('propose', 'Structure proposed', 'The agent plans directories, files and naming conventions', 1, 0),
    mk('approve', 'Structure approval', 'A reviewer approves the structure before any code is written', 2, 1),
    mk('code', 'Code generation', 'The agent writes exactly the approved files', 3, 2),
    mk('commit', 'Code approval & commit', 'Approving the code commits it to GitHub', 4, 3),
  ];
}

export function allDirs(node: CodeNode, acc: string[] = []): string[] {
  if (node.type === 'dir' && node.path) acc.push(node.path);
  node.children?.forEach((c) => allDirs(c, acc));
  return acc;
}

/** Keep only files matching the query (path or purpose) and the folders that lead to them. */
export function filterTree(node: CodeNode, query: string): CodeNode | null {
  const q = query.trim().toLowerCase();
  if (!q) return node;
  if (node.type === 'file') return `${node.path} ${node.purpose}`.toLowerCase().includes(q) ? node : null;
  const kids = (node.children ?? []).map((c) => filterTree(c, q)).filter((c): c is CodeNode => c !== null);
  return kids.length || (node.path === '' ) ? { ...node, children: kids } : null;
}

export function countFiles(node: CodeNode): { files: number; generated: number } {
  if (node.type === 'file') return { files: 1, generated: node.generated ? 1 : 0 };
  return (node.children ?? []).reduce((a, c) => { const n = countFiles(c); return { files: a.files + n.files, generated: a.generated + n.generated }; }, { files: 0, generated: 0 });
}

const LANG: Record<string, string> = {
  py: 'python', ts: 'typescript', tsx: 'typescript', js: 'javascript', jsx: 'javascript', java: 'java', kt: 'kotlin', go: 'go', rs: 'rust',
  cs: 'csharp', rb: 'ruby', php: 'php', sql: 'sql', sh: 'bash', yml: 'yaml', yaml: 'yaml', json: 'json', toml: 'ini', ini: 'ini', md: 'markdown',
  xml: 'xml', html: 'xml', css: 'css', tf: 'hcl', dockerfile: 'dockerfile', gradle: 'groovy',
};
export function langForPath(path: string): string {
  const file = path.split('/').pop() ?? '';
  if (/^dockerfile/i.test(file)) return 'dockerfile';
  const ext = file.includes('.') ? file.split('.').pop()!.toLowerCase() : '';
  return LANG[ext] ?? 'plaintext';
}

export const KIND_BADGE: Record<string, { label: string; cls: string }> = {
  source: { label: 'source', cls: 'bg-sky-50 text-sky-700' }, test: { label: 'test', cls: 'bg-emerald-50 text-emerald-700' },
  config: { label: 'config', cls: 'bg-violet-50 text-violet-700' }, docs: { label: 'docs', cls: 'bg-amber-50 text-amber-700' },
  build: { label: 'build', cls: 'bg-slate-100 text-slate-600' },
};

export function iconFor(path: string): string {
  const l = langForPath(path);
  return ({ python: '🐍', typescript: '🟦', javascript: '🟨', java: '☕', go: '🐹', sql: '🗄️', markdown: '📝', yaml: '⚙️', json: '🧾', dockerfile: '🐳', ini: '⚙️' } as Record<string, string>)[l] ?? '📄';
}
