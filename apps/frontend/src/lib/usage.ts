/** Token usage reports (docs/token-governance.md): the shapes the server returns and the small helpers the Usage page needs. */

export interface UsageTotals {
  tokens: number; calls: number; inputTokens: number; cacheReadTokens: number; cacheWriteTokens: number; outputTokens: number;
  cacheReadShare: number; wasteTokens: number; wasteShare: number; estimatedTokens: number;
}
export interface DayRow { day: string; tokens: number; calls: number }
export interface ActivityRow { activity: string; tokens: number; calls: number; wasteTokens: number }
export interface WasteRow { activity: string; status: string; calls: number; tokens: number }
export interface ModelRow { provider: string; model: string; tokens: number; calls: number }
export interface PersonRow { userId: string | null; name: string; tokens: number; calls: number; share: number; wasteTokens: number; you: boolean }
export interface ProjectUsageReport {
  range: { from: string; to: string; days: number };
  project: { id: string; name: string };
  billable: UsageScope;
  viewer: { id: string; canSeeEveryone: boolean };
  person: string | null;
  totals: UsageTotals; projectTotal: UsageTotals; you: UsageTotals;
  byDay: DayRow[]; byActivity: ActivityRow[]; byStage: Array<{ stage: number | null; tokens: number; calls: number }>;
  byModel: ModelRow[]; byAgent: Array<{ agentId: string; name: string; tokens: number; calls: number }>;
  waste: WasteRow[]; people: PersonRow[];
}
export interface OrgUsageReport {
  range: { from: string; to: string; days: number };
  billable: UsageScope;
  totals: UsageTotals; byDay: DayRow[]; byActivity: ActivityRow[]; byModel: ModelRow[]; waste: WasteRow[];
  byProject: Array<{ projectId: string; name: string; deleted: boolean; tokens: number; calls: number; people: number; wasteTokens: number; wasteShare: number }>;
  unattributed: { calls: number; tokens: number };
}
export interface UsageEvent {
  ts: string; requestId: string; attempt: number; provider: string; model: string; person: string; activity: string; tag: string | null; stage: number | null;
  inputTokens: number; cacheReadTokens: number; cacheWriteTokens: number; outputTokens: number; totalTokens: number; status: string; estimated: boolean; billable: boolean;
  stopReason: string | null; latencyMs: number; providerRequestId: string | null; hasCapture: boolean;
}
export type UsageScope = 'billable' | 'offline' | 'all';

export const ACTIVITY_LABEL: Record<string, string> = {
  'stage.plan': 'Planning a stage', 'stage.clarify': 'Asking clarifying questions', 'stage.generate': 'Writing stage artefacts', 'stage.rework': 'Reworking after validation',
  'stage.validate': 'Validating', 'stage.review': 'Fact and rule checks', 'stage.security_review': 'Security review', 'stage.repair': 'Repairing output',
  'skill.run': 'Skills', 'agent.run': 'Agent runs', 'agent.stage_run': 'Agents inside a stage', 'agent.build': 'Designing agents (audit, test, draft)',
  'code.generate': 'Generating code', 'code.assist': 'Code assistant', 'rules.draft': 'Drafting rules', 'project.advise': 'Project advice (traits, stack)',
  'ingest.read': 'Reading uploaded documents', 'context.compress': 'Summarising context', other: 'Other',
};
export const activityLabel = (a: string): string => ACTIVITY_LABEL[a] ?? a;

export const STATUS_LABEL: Record<string, string> = {
  error: 'Failed', refused: 'Refused by the model', canceled: 'Cancelled', truncated: 'Cut off at the output limit', ok: 'Completed',
  failed: 'Anything that went wrong',
};
export const statusLabel = (s: string): string => STATUS_LABEL[s] ?? s;

export const SCOPE_LABEL: Record<UsageScope, string> = { billable: 'Provider tokens', offline: 'Offline (mock and local)', all: 'Everything' };

export const calls = (n: number): string => `${n.toLocaleString('en-GB')} ${n === 1 ? 'call' : 'calls'}`;

/** Whole-number display with thousands separators; `tokensLabel` in lib/agents.ts is the short form. */
export const tokensFull = (n: number): string => Math.round(n).toLocaleString('en-GB');
export const percent = (share: number): string => {
  const p = share * 100;
  return p > 0 && p < 1 ? '<1%' : `${Math.round(p)}%`;
};

/** Width of each bar as a percentage of the longest, with a visible sliver for anything above zero. */
export function barWidths(values: number[]): number[] {
  const max = Math.max(0, ...values);
  if (max <= 0) return values.map(() => 0);
  return values.map((v) => (v <= 0 ? 0 : Math.max(2, Math.round((v / max) * 100))));
}

/** The days of the range in order, with a zero row for days that had no calls, so the columns line up with the calendar. */
export function fillDays(range: { from: string; to: string }, rows: DayRow[]): DayRow[] {
  const have = new Map(rows.map((r) => [r.day, r]));
  const out: DayRow[] = [];
  const end = Date.parse(`${range.to}T00:00:00Z`);
  for (let t = Date.parse(`${range.from}T00:00:00Z`); t <= end; t += 86_400_000) {
    const day = new Date(t).toISOString().slice(0, 10);
    out.push(have.get(day) ?? { day, tokens: 0, calls: 0 });
  }
  return out;
}

export const RANGES: Array<{ id: string; label: string; days: number }> = [
  { id: '7', label: 'Last 7 days', days: 7 }, { id: '30', label: 'Last 30 days', days: 30 }, { id: '90', label: 'Last 90 days', days: 90 },
];
/** from/to (inclusive, UTC dates) for a preset ending today. */
export function rangeFor(days: number, today: Date = new Date()): { from: string; to: string } {
  const to = today.toISOString().slice(0, 10);
  const from = new Date(Date.parse(`${to}T00:00:00Z`) - (days - 1) * 86_400_000).toISOString().slice(0, 10);
  return { from, to };
}

export function usageQuery(p: { from: string; to: string; billable: UsageScope; person?: string | null; activity?: string | null; status?: string | null; limit?: number; offset?: number }): string {
  const q = new URLSearchParams({ from: p.from, to: p.to, billable: p.billable });
  if (p.person) q.set('person', p.person);
  if (p.activity) q.set('activity', p.activity);
  if (p.status) q.set('status', p.status);
  if (p.limit !== undefined) q.set('limit', String(p.limit));
  if (p.offset !== undefined) q.set('offset', String(p.offset));
  return q.toString();
}

/** One short sentence about where the project's tokens went, for the top of the page. */
export function headline(r: ProjectUsageReport): string {
  const top = r.byActivity[0];
  if (!r.totals.calls) return 'No model calls in this period.';
  const what = top ? `${percent(top.tokens / Math.max(1, r.totals.tokens))} went to ${activityLabel(top.activity).toLowerCase()}` : '';
  return `${tokensFull(r.totals.tokens)} tokens across ${calls(r.totals.calls)}${what ? `; ${what}` : ''}.`;
}

// ------------------------------------------------------------------------------------------------- reconciliation with AWS
export interface ReconRun {
  day: string; status: 'ok' | 'drift' | 'no_data' | 'error'; source: string; triggeredBy: string; finishedAt: string | null; threshold: number;
  convention: string | null; ledgerTokens: number; awsTokens: number; drift: number | null; matched: number; mismatched: number;
  missingInLedger: number; missingInAws: number; unmatchableLedger: number; estimatedPairs: number;
  byModel: Array<{ model: string; ledger: number; aws: number; calls: number; drift: number | null }>; note: string | null; error: string | null;
}
export interface ReconList { configured: boolean; source: string; threshold: number; runs: ReconRun[] }
export interface ReconDiff { kind: 'mismatch' | 'missing_in_ledger' | 'missing_in_aws'; providerRequestId: string | null; model: string | null; ts: string | null; ledgerTokens: number; awsTokens: number; delta: number; projectId: string | null; activity: string | null }

export const RECON_STATUS: Record<ReconRun['status'], { label: string; tone: 'green' | 'red' | 'amber' | 'slate' }> = {
  ok: { label: 'Agrees', tone: 'green' }, drift: { label: 'Differs', tone: 'red' }, no_data: { label: 'No AWS log', tone: 'amber' }, error: { label: 'Could not run', tone: 'amber' },
};
/** Signed difference as a percentage of what AWS reports; "n/a" when AWS reported nothing. */
export function driftLabel(drift: number | null): string {
  if (drift === null) return 'n/a';
  const p = drift * 100;
  if (Math.abs(p) < 0.05) return '0%';
  return `${p > 0 ? '+' : '−'}${Math.abs(p) < 10 ? Math.abs(p).toFixed(1) : Math.round(Math.abs(p))}%`;
}
export const DIFF_LABEL: Record<ReconDiff['kind'], string> = {
  mismatch: 'Different token counts', missing_in_ledger: 'AWS served it; the ledger has no record', missing_in_aws: 'The ledger has it; AWS has no record',
};
/** The sentence for the top of the panel: what the latest night found, or what to do when there is none. */
export function reconHeadline(l: ReconList): string {
  if (!l.configured) return 'Not set up. The ledger is not being checked against AWS.';
  const r = l.runs[0];
  if (!r) return 'Set up. The first comparison runs after the next full UTC day.';
  if (r.status === 'error') return `${r.day}: the comparison could not run. ${r.error ?? ''}`.trim();
  if (r.status === 'no_data') return `${r.day}: AWS returned no invocation log records for this day.`;
  return r.status === 'ok' ? `${r.day}: the ledger agrees with AWS (${driftLabel(r.drift)}).` : `${r.day}: the ledger differs from AWS by ${driftLabel(r.drift)}, more than the ${Math.round(r.threshold * 100)}% allowed.`;
}

/** One request behind a row of the ledger: its attempts and, when it went wrong, what was asked and what came back. */
export interface RequestAttempt {
  attempt: number; provider: string; model: string; region: string | null; providerRequestId: string | null; status: string; errorClass: string | null; stopReason: string | null;
  latencyMs: number; inputTokens: number; cacheReadTokens: number; cacheWriteTokens: number; outputTokens: number; totalTokens: number; estimated: boolean; billable: boolean;
  error: string | null; errorKind: string | null;
}
export interface RequestCapture {
  reason: string; error: string | null; capturedAt: string; expiresAt: string | null;
  messages: Array<{ role: string; content: string }> | null; promptText: string | null; promptCut: boolean; response: string | null; responseCut: boolean;
}
export interface RequestDetail {
  requestId: string; ts: string; person: string | null; activity: string; stage: number | null; tag: string | null; totalTokens: number;
  attempts: RequestAttempt[]; capture: RequestCapture | null; captureNote?: string;
}
export const CAPTURE_REASON: Record<string, string> = {
  error: 'The call failed', unreachable: 'The AI gateway could not be reached', refused: 'The model refused', canceled: 'The call was cancelled',
  truncated: 'The answer was cut off at the output limit', failed_over: 'A provider failed and another one answered',
  invalid_output: 'The model answered, but the answer was rejected',
};
export const captureReason = (r: string): string => CAPTURE_REASON[r] ?? r;
