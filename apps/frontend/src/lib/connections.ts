export type ConnKind = 'github' | 'jira' | 'confluence' | 'kb';
export interface TestCheck { name: string; ok: boolean; detail: string; optional?: boolean }
export interface TestResult { ok: boolean; at: string; by: string; checks: TestCheck[]; usesOwnCredential: boolean }
export interface Connection {
  settings: Record<string, string | boolean | number>; secretSet: boolean; lastTest: TestResult | null; updatedBy: string | null; updatedAt: string | null;
}
export type Connections = Record<ConnKind, Connection>;

export interface FieldDef { key: string; label: string; placeholder?: string; hint?: string; type?: 'text' | 'email' | 'url' }
export interface SectionDef {
  kind: ConnKind; title: string; blurb: string; fields: FieldDef[]; secret?: { key: string; label: string; hint: string };
}

export const SECTIONS: SectionDef[] = [
  {
    kind: 'github', title: 'Git repository', blurb: 'Where this project’s generated code, pipelines and documents are committed.',
    fields: [
      { key: 'repo', label: 'Repository', placeholder: 'owner/name' },
      { key: 'branch', label: 'Branch', placeholder: 'main', hint: 'Leave blank for the repository’s default branch.' },
      { key: 'apiUrl', label: 'API address', placeholder: 'https://api.github.com', type: 'url', hint: 'Only for GitHub Enterprise Server.' },
    ],
    secret: { key: 'token', label: 'Access token', hint: 'A token with write access to the repository. It is stored encrypted and never shown again.' },
  },
  {
    kind: 'jira', title: 'Jira', blurb: 'Where epics, stories and tests for this project are created.',
    fields: [
      { key: 'baseUrl', label: 'Site address', placeholder: 'https://your-company.atlassian.net', type: 'url' },
      { key: 'projectKey', label: 'Project key', placeholder: 'PAY' },
      { key: 'email', label: 'Account email', placeholder: 'you@company.com', type: 'email' },
    ],
    secret: { key: 'apiToken', label: 'API token', hint: 'An Atlassian API token for that account. It is stored encrypted and never shown again.' },
  },
  {
    kind: 'confluence', title: 'Confluence', blurb: 'Where this project’s requirement and design pages are published.',
    fields: [
      { key: 'baseUrl', label: 'Site address', placeholder: 'https://your-company.atlassian.net', type: 'url' },
      { key: 'spaceKey', label: 'Space key', placeholder: 'PAYDOCS' },
      { key: 'email', label: 'Account email', placeholder: 'you@company.com', type: 'email' },
    ],
    secret: { key: 'apiToken', label: 'API token', hint: 'Leave blank if you use the same account as Jira (tick the box above).' },
  },
  {
    kind: 'kb', title: 'Knowledge base', blurb: 'What the agents may search for this project when they write.', fields: [],
  },
];

export type Status = { tone: 'green' | 'red' | 'amber' | 'slate'; label: string };

/** The one-word state shown on a section’s header. */
export function statusOf(kind: ConnKind, c: Connection | undefined): Status {
  if (!c) return { tone: 'slate', label: 'Not set up' };
  if (c.lastTest) return c.lastTest.ok ? { tone: 'green', label: 'Connected' } : { tone: 'red', label: 'Test failed' };
  if (kind === 'kb') return { tone: 'slate', label: 'Default' };
  const s = c.settings;
  const target = kind === 'github' ? s.repo : kind === 'jira' ? s.projectKey : s.spaceKey;
  if (!target) return { tone: 'slate', label: 'Not set up' };
  return c.secretSet ? { tone: 'amber', label: 'Not tested yet' } : { tone: 'slate', label: 'Using the shared connection' };
}

/** True when the form differs from what is saved (or a new credential was typed). */
export function isDirty(saved: Connection | undefined, draft: Record<string, string | boolean | number>, secret: string): boolean {
  if (secret.trim()) return true;
  const base = saved?.settings ?? {};
  return Object.keys(draft).some((k) => (draft[k] ?? '') !== (base[k] ?? ''));
}
