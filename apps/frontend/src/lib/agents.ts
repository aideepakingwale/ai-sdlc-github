// Custom agents and skills: the shapes the API returns and the small pure helpers the builder, the lists and the stage settings share.

export type DefKind = 'agent' | 'skill';
export type VersionStatus = 'draft' | 'pending' | 'published' | 'rejected' | 'superseded';
export type ModelRole = 'reason' | 'generate' | 'light' | 'vision';
export type ValueType = 'string' | 'number' | 'boolean' | 'object' | 'list';
export type Format = 'JSON' | 'Markdown' | 'Text';

export interface Rights { view: boolean; manage?: boolean; edit: boolean; approve: boolean }
export interface InputDef { name: string; type: ValueType; source: string; required: boolean; description?: string }
export interface OutputDef { name: string; type: ValueType; artefact_type: string; format: Format }
export interface ChildDef { agent_id: string; version?: number | null; when: string }
export interface AgentBody {
  description: string; prompt: string; role: ModelRole; model: string | null; fallback: string | null; temperature: number; icon: string;
  execution_mode: 'native_llm'; tools: string[]; inputs: InputDef[]; outputs: OutputDef[];
  stage_kind?: 'specialist' | 'stage'; children?: ChildDef[]; roles?: string[]; stages?: number[];
}
export interface Finding {
  id: string; area: 'Capability' | 'Ambiguity' | 'Contradiction' | 'Security'; severity: 'block' | 'warn' | 'pass'; guardrail: string | null;
  title: string; detail: string; snippet: string; fix: null | { label: string; find: string; replace: string }; source: 'check' | 'model' | 'probe';
}
export interface Probe { id: string; title: string; held: boolean }
export interface AuditReport {
  ran_at: string; stale: boolean; ack: boolean; findings: Finding[]; probes: Probe[]; notes: string[];
  summary: { block: number; warn: number; pass: number }; tokens: { promptTokens: number; completionTokens: number };
}
export interface VersionView {
  version: number; status: VersionStatus; author: string; submittedBy: string; submittedById?: string | null; submittedAt: string | null; decidedBy: string; decidedAt: string | null;
  comment: string; updatedAt: string | null; audit: null | { ranAt: string; stale: boolean; ack: boolean; summary: AuditReport['summary'] };
  body?: AgentBody; auditReport?: AuditReport | null;
}
export interface DefView {
  id: string; kind: DefKind; scope: 'org' | 'project'; projectId: string | null; name: string; open: boolean; retired: boolean; createdBy: string; createdAt: string | null;
  updatedAt: string | null; source: null | { kind: 'core' | 'def'; id: string; name: string; version: number | null };
}
export interface Guardrail { id: string; text: string; severity: 'block' | 'warn'; default: 'block' | 'warn' }
export interface DefDetail {
  def: DefView; rights: Rights; current: VersionView | null; published: VersionView | null; versions: VersionView[]; usedIn: Array<[string, string]>;
  cases: Array<{ id: string; name: string; inputs: Record<string, unknown> }>; newerSource: null | { name: string; version: number }; canSubmit: boolean; guardrails: Guardrail[];
}
export interface Card extends DefView {
  status: VersionStatus; version: number; publishedVersion: number | null; author: string; usedInStages: number; usedInProjects: number;
  description: string; role: ModelRole; roleLabel: string; inputs: string[]; outputs: string[]; children: number; roles: string[]; stages: number[];
}
export interface CoreCard {
  id: string; kind: DefKind; source: 'core'; name: string; description: string; version: number; role: ModelRole; roleLabel: string; stage?: number | null;
  category?: string; runtime?: string; inputs: string[]; outputs: string[]; roles?: string[]; file?: string;
}
export interface RunResultView {
  outputs: Record<string, unknown>; tokens: { promptTokens: number; completionTokens: number }; totalTokens: number; provider: string; model: string; mock: boolean;
  warnings: string[]; context: Array<{ label: string; chars: number }>; systemPrompt?: string; userPrompt?: string;
  tree: { id: string; name: string; tokens: number; skipped: Array<{ agent: string; why: string }>; children: RunResultView['tree'][] };
}
export interface PendingItem {
  defId: string; name: string; kind: DefKind; scope: 'org' | 'project'; version: number; submittedBy: string; submittedById?: string | null; submittedAt: string | null;
  audit: Partial<AuditReport['summary']>; acknowledged: boolean; source?: string | null; previousPrompt: string;
}
export interface StageItem {
  defId: string; name: string; kind: DefKind; source: 'org' | 'project'; pinnedVersion: number; publishedVersion: number | null; newerAvailable: boolean;
  runs: 'always' | 'when' | 'on_request'; condition: string; roles: string[]; description: string; roleLabel: string; inputs: string[]; outputs: string[];
  outputsDetail: OutputDef[]; childrenDetail: string[];
}
export interface StageAvailable { defId: string; name: string; kind: DefKind; source: 'org' | 'project'; version: number; description: string }

export const MODEL_ROLES: Array<{ id: ModelRole; label: string; hint: string }> = [
  { id: 'reason', label: 'Reasoning', hint: 'Decisions and analysis' }, { id: 'generate', label: 'Generation', hint: 'Documents and code' },
  { id: 'light', label: 'Fast', hint: 'Short lists and simple tasks' }, { id: 'vision', label: 'Vision', hint: 'Reads images and diagrams' },
];
export const ROLE_LABEL: Record<string, string> = Object.fromEntries(MODEL_ROLES.map((r) => [r.id, r.label]));
export const VALUE_TYPES: ValueType[] = ['string', 'number', 'boolean', 'object', 'list'];
export const FORMATS: Format[] = ['JSON', 'Markdown', 'Text'];
export const ARTEFACT_TYPES = ['REPORT', 'RISK_ASSESSMENT', 'CHECKLIST', 'CUSTOM_DATA', 'DELIVERABLE'];
export const UPSTREAM_TYPES = ['PRD', 'EPIC', 'FEATURE', 'USER_STORY', 'HLD', 'ADR', 'LLD', 'OPENAPI', 'DBML', 'TEST_STRATEGY', 'RTM', 'PIPELINE_DESIGN'];
export const PHASE_ROLES = ['PO', 'SA', 'TA', 'QA', 'DEVOPS', 'DEV'];
export const MODELS = ['Anthropic · Claude Sonnet', 'AWS Bedrock · Claude Haiku', 'OpenAI · GPT-4o mini', 'Local · Llama 3 (Ollama)'];
export const STAGE_NAMES: Record<number, string> = { 1: 'Requirements', 2: 'Solution architecture', 3: 'Technical design', 4: 'Test engineering', 5: 'CI/CD', 6: 'Implementation', 7: 'Custom' };

const STATUS: Record<VersionStatus, { label: string; tone: 'slate' | 'amber' | 'green' | 'red' }> = {
  draft: { label: 'Draft', tone: 'slate' }, pending: { label: 'Needs approval', tone: 'amber' }, published: { label: 'Approved', tone: 'green' },
  rejected: { label: 'Changes requested', tone: 'red' }, superseded: { label: 'Replaced', tone: 'slate' },
};
export function statusChip(status: VersionStatus, version?: number): { label: string; tone: 'slate' | 'amber' | 'green' | 'red' } {
  const s = STATUS[status];
  return status === 'published' && version ? { ...s, label: `Approved v${version}` } : s;
}

/** The four steps of a version's life, and which one it is on. */
export const STEPS = ['Draft', 'Audit', 'Approval', 'Published'] as const;
export function stepIndex(status: VersionStatus, auditFresh: boolean): number {
  if (status === 'published' || status === 'superseded') return 3;
  if (status === 'pending') return 2;
  return auditFresh ? 1 : 0;
}

export const sourceLabel = (src: string): string =>
  src === 'brief' ? 'Stage brief' : src === 'user' ? 'Typed or pinned when run' : src === 'context:rules' ? 'Project rules' : src === 'context:stack' ? 'Project stack'
  : src.startsWith('upstream:') ? `Upstream ${src.slice(9)}` : src;
export const SOURCE_OPTIONS: Array<{ id: string; label: string }> = [
  { id: 'brief', label: 'Stage brief' }, { id: 'user', label: 'Typed or pinned when run' }, { id: 'context:rules', label: 'Project rules' }, { id: 'context:stack', label: 'Project stack' },
  ...UPSTREAM_TYPES.map((t) => ({ id: `upstream:${t}`, label: `Upstream ${t}` })),
];

// ---- the prompt: variables as pills
export interface Segment { text: string; variable?: string; declared?: boolean }
const VAR = /\{([A-Za-z_][A-Za-z0-9_]*)\}/g;
const BRACE = /\{\{[\s\S]+?\}\}/g;

export function variablesIn(prompt: string): string[] {
  return [...new Set([...prompt.replace(BRACE, '').matchAll(VAR)].map((m) => m[1]!))];
}

/** The prompt as pieces: plain text and {variables}, each variable marked as declared or not (the builder shows an undeclared one in red). */
export function segments(prompt: string, declared: string[]): Segment[] {
  const out: Segment[] = [];
  let i = 0;
  const masked = prompt.replace(BRACE, (m) => ' '.repeat(m.length));
  for (const m of masked.matchAll(VAR)) {
    if (m.index! > i) out.push({ text: prompt.slice(i, m.index) });
    out.push({ text: m[0], variable: m[1], declared: declared.includes(m[1]!) });
    i = m.index! + m[0].length;
  }
  if (i < prompt.length) out.push({ text: prompt.slice(i) });
  return out;
}

export function insertAt(text: string, pos: number, add: string): { text: string; cursor: number } {
  const p = Math.max(0, Math.min(pos, text.length));
  return { text: text.slice(0, p) + add + text.slice(p), cursor: p + add.length };
}
export function appendLine(text: string, line: string): string { return `${text.replace(/\s+$/, '')}\n${line}`; }

export function uniqueName(prefix: string, taken: string[]): string {
  let n = taken.length + 1;
  while (taken.includes(`${prefix}_${n}`)) n += 1;
  return `${prefix}_${n}`;
}

export function sampleValue(t: ValueType): unknown {
  return t === 'object' ? { example: 'value' } : t === 'list' ? ['item one', 'item two'] : t === 'number' ? 1 : t === 'boolean' ? true : 'example text';
}
export const sampleInputs = (inputs: InputDef[]): Record<string, unknown> => Object.fromEntries(inputs.map((i) => [i.name, sampleValue(i.type)]));

const ROLE_COST: Record<ModelRole, number> = { reason: 0.02, generate: 0.008, light: 0.001, vision: 0.012 };
const ROLE_SECONDS: Record<ModelRole, number> = { reason: 9, generate: 5, light: 2, vision: 6 };
export function costHint(role: ModelRole, delegates: number): { dollars: string; seconds: number } {
  return { dollars: (ROLE_COST[role] + delegates * 0.004).toFixed(3), seconds: ROLE_SECONDS[role] + delegates * 2 };
}

// ---- the palette: what is dragged, and what dropping it does
export type PaletteKind = 'input' | 'output' | 'var' | 'snippet' | 'agent' | 'stageagent';
export interface PalettePayload { kind: PaletteKind; value: string }
export const encodePalette = (p: PalettePayload): string => `${p.kind}|${p.value}`;
export function decodePalette(s: string): PalettePayload | null {
  const i = s.indexOf('|');
  if (i < 0) return null;
  const kind = s.slice(0, i) as PaletteKind;
  return ['input', 'output', 'var', 'snippet', 'agent', 'stageagent'].includes(kind) ? { kind, value: s.slice(i + 1) } : null;
}
export const SNIPPETS: Array<{ label: string; text: string }> = [
  { label: 'Success criteria', text: 'A good answer: states the conclusion first, then the evidence, and says what it could not check.' },
  { label: 'Output format', text: 'Reply only with the declared outputs. Do not add commentary.' },
  { label: 'Threshold', text: 'Treat a score of 0.7 or higher as high.' },
];

// ---- the definition as developer-mode JSON
export function toDeveloperJson(name: string, b: AgentBody): string {
  return JSON.stringify({
    identity: { name, description: b.description, system_prompt: b.prompt }, execution_mode: b.execution_mode,
    cognitive_engines: { primary: { role: b.role, model: b.model, temperature: b.temperature }, fallback: b.fallback },
    schema: {
      inputs: Object.fromEntries(b.inputs.map((i) => [i.name, { type: i.type, required: i.required, source: i.source }])),
      outputs: Object.fromEntries(b.outputs.map((o) => [o.name, { type: o.type, artefact: o.artefact_type, format: o.format }])),
    },
    delegation: (b.children ?? []).map((c) => ({ agent: c.agent_id, when: c.when })), ...(b.roles ? { access: { roles: b.roles, stages: b.stages ?? [] } } : {}),
  }, null, 2);
}

/** Apply developer-mode JSON to a definition. Returns the new name and body, or a message that says what is wrong. */
export function fromDeveloperJson(text: string, base: AgentBody): { ok: true; name: string; body: AgentBody } | { ok: false; error: string } {
  type J = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any -- developer JSON is untrusted and checked field by field
  let o: J;
  try { o = JSON.parse(text) as J; } catch (e) { return { ok: false, error: e instanceof Error ? e.message : 'Not valid JSON' }; }
  if (!o || typeof o !== 'object' || !o.identity || typeof o.identity.system_prompt !== 'string') return { ok: false, error: 'identity.system_prompt is required' };
  const eng = o.cognitive_engines?.primary ?? {};
  const inputs = Object.entries<J>(o.schema?.inputs ?? {}).map(([name, v]) => ({ name, type: v?.type ?? 'string', source: v?.source ?? 'brief', required: v?.required !== false, description: v?.description ?? '' }));
  const outputs = Object.entries<J>(o.schema?.outputs ?? {}).map(([name, v]) => ({ name, type: v?.type ?? 'string', artefact_type: v?.artefact ?? 'REPORT', format: v?.format ?? 'Markdown' }));
  const children = Array.isArray(o.delegation) ? o.delegation.map((c: J) => ({ agent_id: String(c.agent ?? ''), when: String(c.when ?? 'always') })) : base.children;
  return {
    ok: true, name: String(o.identity.name ?? ''),
    body: { ...base, description: String(o.identity.description ?? ''), prompt: o.identity.system_prompt, role: eng.role ?? base.role, model: eng.model ?? null, temperature: typeof eng.temperature === 'number' ? eng.temperature : base.temperature,
            fallback: o.cognitive_engines?.fallback ?? null, inputs, outputs, ...(base.children !== undefined ? { children } : {}),
            ...(o.access ? { roles: o.access.roles ?? [], stages: o.access.stages ?? [] } : {}) },
  };
}

export const countFindings = (r: AuditReport | null | undefined): { block: number; warn: number } => ({ block: r?.summary.block ?? 0, warn: r?.summary.warn ?? 0 });

/** Can this audit be submitted as it stands? (The server decides too; this only enables the button.) */
export function auditSubmittable(a: VersionView['audit'] | null | undefined): boolean {
  return Boolean(a && !a.stale && (a.summary.block ?? 0) === 0 && ((a.summary.warn ?? 0) === 0 || a.ack));
}

/** Delegation limits, for the meters in the builder. */
export const LIMITS = { depth: 2, children: 5, tokensK: 40 } as const;
export function delegationMeters(children: number): Array<{ label: string; value: number; max: number }> {
  return [{ label: 'Depth', value: children ? 1 : 0, max: LIMITS.depth }, { label: 'Delegates', value: children, max: LIMITS.children }, { label: 'Token budget (k)', value: 12 + children * 7, max: LIMITS.tokensK }];
}

export function lineDiff(before: string, after: string): Array<{ kind: 'same' | 'add' | 'del'; text: string }> {
  const a = before.split('\n').filter(Boolean), b = after.split('\n').filter(Boolean);
  return [...a.filter((l) => !b.includes(l)).map((text) => ({ kind: 'del' as const, text })), ...b.map((text) => ({ kind: (a.includes(text) ? 'same' : 'add') as 'same' | 'add', text }))];
}
