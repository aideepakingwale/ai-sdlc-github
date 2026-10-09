/**
 * Pure model for the Rules, Templates and "What agents see" tabs of Project Context: the shapes the API returns, list filtering and sorting,
 * the compliance words, and small helpers for drafts and templates. No React, no DOM.
 */
export type RuleCategory = 'rule' | 'decision' | 'glossary' | 'constraint' | 'preference';
export type RulePriority = 'must' | 'should' | 'context';
export interface Rule {
  id: string; category: RuleCategory; priority: RulePriority; stage: number | null; title: string; body: string; active: boolean;
  origin?: string | null; scope: 'project' | 'org'; createdAt: string; optedOut?: boolean; optOutReason?: string;
}
export interface Pack { id: string; name: string; description: string; count: number; alreadyHave: number; stages: number[] }
export interface RuleCheck { complied: number; violated: number; unclear: number; violations?: Array<{ phase: number; artefact: string | null; evidence: string | null }> }
export interface Compliance { rules: Record<string, RuleCheck>; artefacts: Record<string, { complied: number; violated: number; unclear: number; items: Array<{ rule: string; status: string; evidence: string | null }> }> }
export interface RuleDraft { category: RuleCategory; priority: RulePriority; stage: number | null; title: string; body: string }
export interface RuleHints { duplicates: Array<{ id: string; title: string; scope: string }>; conflicts: Array<{ id: string; title: string; why: string }>; stack: Array<{ layer: string; rule: string; pinned: string }>; ok: boolean }

export const CATEGORIES: RuleCategory[] = ['rule', 'decision', 'glossary', 'constraint', 'preference'];
export const PRIORITIES: RulePriority[] = ['must', 'should', 'context'];
export const PRIORITY_WORD: Record<RulePriority, string> = { must: 'Must', should: 'Should', context: 'Background' };
export const PRIORITY_TONE: Record<RulePriority, 'red' | 'amber' | 'slate'> = { must: 'red', should: 'amber', context: 'slate' };
export const STAGE_NAMES: Record<number, string> = { 1: 'Requirements', 2: 'Solution architecture', 3: 'Technical design', 4: 'Test engineering', 5: 'CI/CD', 6: 'Implementation' };

export interface RuleFilter { query: string; stage: 'all' | 'every' | number; priority: 'all' | RulePriority; category: 'all' | RuleCategory }
export const NO_FILTER: RuleFilter = { query: '', stage: 'all', priority: 'all', category: 'all' };

const order = (p: RulePriority) => ({ must: 0, should: 1, context: 2 })[p];

/** Search the title and body, narrow by stage ('every' = rules for all stages), priority and category; most binding first. */
export function filterRules<T extends Rule>(rules: T[], f: RuleFilter): T[] {
  const q = f.query.trim().toLowerCase();
  return rules
    .filter((r) => (f.stage === 'all' ? true : f.stage === 'every' ? r.stage === null : r.stage === null || r.stage === f.stage))
    .filter((r) => (f.priority === 'all' || r.priority === f.priority) && (f.category === 'all' || r.category === f.category))
    .filter((r) => !q || `${r.title} ${r.body}`.toLowerCase().includes(q))
    .slice()
    .sort((a, b) => order(a.priority) - order(b.priority) || a.title.localeCompare(b.title));
}

/** What the last check found for a rule, in words. */
export function checkWord(c: RuleCheck | undefined): { text: string; tone: 'green' | 'red' | 'slate' } | null {
  if (!c) return null;
  if (c.violated > 0) return { text: c.violated === 1 ? 'Not followed in 1 stage' : `Not followed in ${c.violated} stages`, tone: 'red' };
  if (c.complied > 0) return { text: c.complied === 1 ? 'Followed in 1 stage' : `Followed in ${c.complied} stages`, tone: 'green' };
  return { text: 'Could not tell', tone: 'slate' };
}

export const originLabel = (o?: string | null): string => (!o ? '' : o.startsWith('pack:') ? 'From a rule pack' : o.startsWith('memory:') ? 'From team memory' : o === 'draft' ? 'Drafted from a document' : '');

export const hasHints = (h: RuleHints | null): boolean => Boolean(h && (h.duplicates.length || h.conflicts.length || h.stack.length));

/** Roughly four characters to a token. A label on the screen says it is an estimate. */
export const tokens = (n: number): string => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));

// ---- templates
export interface Template {
  id: string; scope: 'project' | 'platform'; artefactType: string; outputFormat: string; name: string; template: string;
  analysis: { sections?: string[]; placeholders?: string[]; jsonKeys?: string[]; lineCount?: number; hasTables?: boolean };
  usage?: { count: number; phases: number[] }; overrides?: boolean; versions?: number;
}
export interface TemplateSuggestion { artefactType: string | null; candidates: string[]; outputFormat: string; name: string; sections: string[]; placeholders: string[]; lineCount: number }
export const FORMATS = ['markdown', 'json', 'yaml', 'xml', 'html', 'text'] as const;
export const COMMON_TYPES = ['PRD', 'EPIC', 'USER_STORY', 'HLD', 'LLD', 'ADR', 'OPENAPI', 'DBML', 'TEST_STRATEGY', 'RTM', 'PIPELINE_DESIGN', 'PULL_REQUEST'];

export function usedBy(t: Pick<Template, 'usage'>): string {
  const u = t.usage;
  if (!u || u.count === 0) return 'Not used yet';
  return `${u.count} artefact${u.count === 1 ? '' : 's'}${u.phases.length ? ` in stage ${u.phases.join(', ')}` : ''}`;
}
