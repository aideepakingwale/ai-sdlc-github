import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState, type ReactNode } from 'react';
import { api } from '../../api/client';
import { tokensLabel } from '../../lib/agents';
import { Pill } from '../bits';
import BalanceMeter from './BalanceMeter';
import PoolsPanel from './PoolsPanel';
import {
  activityLabel, barWidths, calls, DIFF_LABEL, driftLabel, reconHeadline, RECON_STATUS, type ReconDiff, type ReconList, type ReconRun, fillDays, headline, percent, RANGES, rangeFor, SCOPE_LABEL, statusLabel, tokensFull, usageQuery,
  type OrgUsageReport, type ProjectUsageReport, type UsageEvent, type UsageScope, type UsageTotals, type WasteRow,
} from '../../lib/usage';

/**
 * Token usage. A project's page shows what the project spent and who spent it (each person's line only inside this project);
 * an administrator can switch to the organisation, which stops at projects. Everything here is a number from the token ledger.
 */
export default function UsagePage({ projectId: openProject, isAdmin, startOnOrg = false }: { projectId: string | null; isAdmin: boolean; startOnOrg?: boolean }) {
  // An administrator can look into any project from the organisation view without switching the project they have open.
  const [picked, setPicked] = useState<string | null>(null);
  const projectId = picked ?? openProject;
  const [tab, setTab] = useState<'project' | 'org'>(!projectId || (isAdmin && startOnOrg) ? 'org' : 'project');
  const [days, setDays] = useState('30');
  const [scope, setScope] = useState<UsageScope>('billable');
  const range = useMemo(() => rangeFor(RANGES.find((r) => r.id === days)?.days ?? 30), [days]);
  const showOrg = isAdmin && (tab === 'org' || !projectId);
  return (
    <div className="h-full overflow-y-auto bg-slate-50 p-6" data-testid="v2-usage-page">
      <div className="mx-auto w-full max-w-5xl">
        <div className="mb-1 flex flex-wrap items-end justify-between gap-3">
          <h1 className="font-display text-xl font-bold text-navy">Token usage</h1>
          <div className="flex flex-wrap items-center gap-2 text-sm">
            {isAdmin && projectId && (
              <div className="mr-2 inline-flex overflow-hidden rounded-lg border border-slate-300" role="tablist">
                {(['project', 'org'] as const).map((t) => (
                  <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)} data-testid={`v2-usage-tab-${t}`}
                    className={`px-3 py-1 ${tab === t ? 'bg-brand-600 text-white' : 'bg-white text-slate-600 hover:bg-slate-100'}`}>{t === 'project' ? 'This project' : 'Whole organisation'}</button>
                ))}
              </div>
            )}
            <select value={days} onChange={(e) => setDays(e.target.value)} aria-label="Period" className="rounded-lg border border-slate-300 bg-white px-2 py-1" data-testid="v2-usage-range">
              {RANGES.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
            </select>
            <select value={scope} onChange={(e) => setScope(e.target.value as UsageScope)} aria-label="Which tokens" className="rounded-lg border border-slate-300 bg-white px-2 py-1" data-testid="v2-usage-scope">
              {(Object.keys(SCOPE_LABEL) as UsageScope[]).map((s) => <option key={s} value={s}>{SCOPE_LABEL[s]}</option>)}
            </select>
          </div>
        </div>
        {!showOrg && picked && picked !== openProject && <p className="mb-1 text-sm"><button type="button" className="text-brand-600 hover:underline" onClick={() => { setPicked(null); setTab('org'); }}>← All projects</button></p>}
        <p className="mb-4 text-sm text-slate-500">
          {showOrg ? 'What each project spent. Open a project to see who spent it.' : 'Tokens the model processed for this project: input, cached input and output. Each person’s figure is what they used in this project.'}
          {scope !== 'billable' && <span className="ml-1 font-medium text-amber-700">Showing {SCOPE_LABEL[scope].toLowerCase()}.</span>}
        </p>
        {showOrg ? <OrgView range={range} scope={scope} onOpenProject={(id) => { setPicked(id); setTab('project'); }} /> : projectId ? <ProjectView projectId={projectId} range={range} scope={scope} /> : null}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------- a project
function ProjectView({ projectId, range, scope }: { projectId: string; range: { from: string; to: string }; scope: UsageScope }) {
  const [person, setPerson] = useState<string>('');
  const [showCalls, setShowCalls] = useState<{ activity?: string; status?: string } | null>(null);
  const qs = usageQuery({ ...range, billable: scope, person: person || null });
  const q = useQuery({ queryKey: ['usage', projectId, qs], queryFn: () => api.get<ProjectUsageReport>(`/api/projects/${projectId}/usage?${qs}`), placeholderData: keepPreviousData });
  const r = q.data;
  if (q.isError) return <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700" role="alert">{q.error instanceof Error ? q.error.message : 'Could not load usage'}</div>;
  if (!r) return <div className="animate-pulse text-sm text-slate-400">Loading…</div>;
  const manager = r.viewer.canSeeEveryone;
  const who = r.person ? (r.people.find((p) => p.userId === r.person)?.name ?? 'This person') : null;
  const mineOnly = Boolean(person) && !manager;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-navy" data-testid="v2-usage-headline"><strong>{r.project.name}</strong>: {who ? `${who}: ` : ''}{headline(r)}</p>
        <div className="flex items-center gap-2 text-sm">
          {manager ? (
            <select value={person} onChange={(e) => setPerson(e.target.value)} aria-label="Person" className="rounded-lg border border-slate-300 bg-white px-2 py-1" data-testid="v2-usage-person">
              <option value="">Everyone in this project</option>
              {r.people.map((p) => <option key={p.userId ?? 'none'} value={p.userId ?? ''} disabled={!p.userId}>{p.name}</option>)}
            </select>
          ) : (
            <label className="flex items-center gap-1 text-slate-600"><input type="checkbox" checked={mineOnly} onChange={(e) => setPerson(e.target.checked ? 'me' : '')} data-testid="v2-usage-justme" /> Just me</label>
          )}
          <a href={`/api/projects/${projectId}/usage/export.csv?${qs}`} download className="rounded-lg border border-slate-300 bg-white px-3 py-1 font-medium text-navy hover:bg-slate-100" data-testid="v2-usage-csv">Download CSV</a>
        </div>
      </div>

      <BalanceMeter projectId={projectId} variant="card" />
      <Tiles t={r.totals} extra={r.person ? { label: 'Whole project', value: tokensLabel(r.projectTotal.tokens), sub: `${tokensFull(r.projectTotal.tokens)} tokens` } : !manager ? { label: 'You', value: tokensLabel(r.you.tokens), sub: 'of the project total' } : undefined} />
      <Days rows={fillDays(r.range, r.byDay)} />

      <div className="grid gap-4 md:grid-cols-2">
        {manager && !r.person && (
          <Panel title="People in this project" hint="What each person used here, as a share of the project" testid="v2-usage-people">
            <Bars rows={r.people.map((p) => ({ key: p.userId ?? 'none', label: p.name + (p.you ? ' (you)' : ''), value: p.tokens, note: `${percent(p.share)} · ${calls(p.calls)}`, strong: p.you }))} />
          </Panel>
        )}
        {!manager && !r.person && (
          <Panel title="You in this project" hint="Your line next to the project total" testid="v2-usage-people">
            <Bars rows={[{ key: 'you', label: 'You', value: r.you.tokens, note: `${percent(r.projectTotal.tokens ? r.you.tokens / r.projectTotal.tokens : 0)} of the project`, strong: true }, { key: 'proj', label: 'Whole project', value: r.projectTotal.tokens, note: calls(r.projectTotal.calls) }]} />
          </Panel>
        )}
        <Panel title="What it was spent on" hint="By kind of work" testid="v2-usage-activity">
          <Bars rows={r.byActivity.map((a) => ({ key: a.activity, label: activityLabel(a.activity), value: a.tokens, note: calls(a.calls) }))} onPick={(k) => setShowCalls({ activity: k })} />
        </Panel>
        <Panel title="By stage" hint="Calls made while working on each stage" testid="v2-usage-stage">
          <Bars rows={r.byStage.map((s) => ({ key: String(s.stage ?? 'none'), label: s.stage == null ? 'Not tied to a stage' : `Stage ${s.stage}`, value: s.tokens, note: calls(s.calls) }))} />
        </Panel>
        <Panel title="By model" hint="Which model served the tokens" testid="v2-usage-model">
          <Bars rows={r.byModel.map((m) => ({ key: `${m.provider}/${m.model}`, label: m.model ? `${m.model}` : m.provider, value: m.tokens, note: `${m.provider} · ${calls(m.calls)}` }))} />
        </Panel>
        {r.byAgent.length > 0 && (
          <Panel title="MindDesigner agents and skills" hint="Tokens used by each agent when it ran" testid="v2-usage-agents">
            <Bars rows={r.byAgent.map((a) => ({ key: a.agentId, label: a.name, value: a.tokens, note: calls(a.calls) }))} />
          </Panel>
        )}
      </div>

      <Waste rows={r.waste} totals={r.totals} onPick={(w) => setShowCalls({ activity: w.activity, status: w.status })} />
      <Calls projectId={projectId} range={range} scope={scope} person={person} filter={showCalls} onFilter={setShowCalls} activities={r.byActivity.map((a) => a.activity)} />
    </div>
  );
}

function Waste({ rows, totals, onPick }: { rows: WasteRow[]; totals: UsageTotals; onPick: (w: WasteRow) => void }) {
  return (
    <Panel title="Tokens that did not produce a result" hint="Calls that failed, were refused, cancelled or cut off at the output limit. They are still counted." testid="v2-usage-waste">
      {rows.length === 0 ? <p className="text-sm text-slate-500">None in this period.</p> : (
        <>
          <p className="mb-2 text-sm text-slate-600">{tokensFull(totals.wasteTokens)} tokens, {percent(totals.wasteShare)} of the total.</p>
          <table className="w-full text-sm"><thead><tr className="text-left text-xs uppercase tracking-wide text-slate-500"><th className="py-1">Work</th><th>What happened</th><th className="text-right">Calls</th><th className="text-right">Tokens</th></tr></thead>
            <tbody>{rows.map((w) => (
              <tr key={`${w.activity}/${w.status}`} className="border-t border-slate-200 hover:bg-slate-50">
                <td className="py-1"><button type="button" className="text-left text-brand-600 hover:underline" onClick={() => onPick(w)}>{activityLabel(w.activity)}</button></td>
                <td>{statusLabel(w.status)}</td><td className="text-right tabular-nums">{w.calls}</td><td className="text-right tabular-nums">{tokensFull(w.tokens)}</td></tr>))}</tbody></table>
        </>
      )}
    </Panel>
  );
}

function Calls({ projectId, range, scope, person, filter, onFilter, activities }: {
  projectId: string; range: { from: string; to: string }; scope: UsageScope; person: string;
  filter: { activity?: string; status?: string } | null; onFilter: (f: { activity?: string; status?: string } | null) => void; activities: string[];
}) {
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState(0);
  const PAGE = 50;
  const isOpen = open || filter !== null;
  const qs = usageQuery({ ...range, billable: scope, person: person || null, activity: filter?.activity, status: filter?.status, limit: PAGE, offset: page * PAGE });
  const q = useQuery({ queryKey: ['usage-events', projectId, qs], enabled: isOpen, queryFn: () => api.get<{ events: UsageEvent[] }>(`/api/projects/${projectId}/usage/events?${qs}`), placeholderData: keepPreviousData });
  return (
    <section className="rounded-xl border border-slate-300 bg-white p-4" data-testid="v2-usage-calls">
      <button type="button" className="flex w-full items-center justify-between text-left" onClick={() => { setOpen(!isOpen); if (isOpen) onFilter(null); setPage(0); }} aria-expanded={isOpen} data-testid="v2-usage-calls-toggle">
        <span><span className="block font-display text-base font-bold text-navy">The calls behind these numbers</span><span className="block text-xs text-slate-500">One row per attempt, newest first</span></span>
        <span className="text-sm text-brand-600">{isOpen ? 'Hide' : 'Show'}</span>
      </button>
      {isOpen && (
        <div className="mt-3">
          <div className="mb-2 flex flex-wrap gap-2 text-sm">
            <select value={filter?.activity ?? ''} onChange={(e) => { onFilter({ ...filter, activity: e.target.value || undefined }); setPage(0); }} aria-label="Kind of work" className="rounded-lg border border-slate-300 bg-white px-2 py-1">
              <option value="">All kinds of work</option>{activities.map((a) => <option key={a} value={a}>{activityLabel(a)}</option>)}
            </select>
            <select value={filter?.status ?? ''} onChange={(e) => { onFilter({ ...filter, status: e.target.value || undefined }); setPage(0); }} aria-label="Outcome" className="rounded-lg border border-slate-300 bg-white px-2 py-1">
              <option value="">Any outcome</option>{['error', 'refused', 'canceled', 'truncated'].map((s) => <option key={s} value={s}>{statusLabel(s)}</option>)}
            </select>
          </div>
          {!q.data ? <div className="animate-pulse text-sm text-slate-400">Loading…</div> : q.data.events.length === 0 ? <p className="text-sm text-slate-500">No calls match.</p> : (
            <div className="overflow-x-auto"><table className="w-full text-xs"><thead><tr className="text-left uppercase tracking-wide text-slate-500"><th className="py-1">When</th><th>Person</th><th>Work</th><th>Model</th><th className="text-right">Input</th><th className="text-right">Cached</th><th className="text-right">Output</th><th className="text-right">Total</th><th>Outcome</th></tr></thead>
              <tbody>{q.data.events.map((e, i) => (
                <tr key={`${e.requestId}-${e.attempt}-${i}`} className="border-t border-slate-200">
                  <td className="whitespace-nowrap py-1">{new Date(e.ts).toLocaleString()}</td><td>{e.person}</td><td>{activityLabel(e.activity)}</td><td>{e.model || e.provider}</td>
                  <td className="text-right tabular-nums">{tokensFull(e.inputTokens)}</td><td className="text-right tabular-nums">{tokensFull(e.cacheReadTokens + e.cacheWriteTokens)}</td>
                  <td className="text-right tabular-nums">{tokensFull(e.outputTokens)}</td><td className="text-right font-medium tabular-nums">{tokensFull(e.totalTokens)}{e.estimated ? ' ~' : ''}</td>
                  <td>{statusLabel(e.status)}{e.attempt > 1 ? ` (attempt ${e.attempt})` : ''}</td></tr>))}</tbody></table></div>
          )}
          <div className="mt-2 flex items-center justify-between text-sm">
            <button type="button" disabled={page === 0} onClick={() => setPage(page - 1)} className="rounded-lg border border-slate-300 px-3 py-1 disabled:opacity-40">Newer</button>
            <span className="text-slate-500">Page {page + 1}</span>
            <button type="button" disabled={(q.data?.events.length ?? 0) < PAGE} onClick={() => setPage(page + 1)} className="rounded-lg border border-slate-300 px-3 py-1 disabled:opacity-40">Older</button>
          </div>
        </div>
      )}
    </section>
  );
}

// ------------------------------------------------------------------------------------------------- the organisation
function OrgView({ range, scope, onOpenProject }: { range: { from: string; to: string }; scope: UsageScope; onOpenProject?: (id: string) => void }) {
  const qs = usageQuery({ ...range, billable: scope });
  const q = useQuery({ queryKey: ['usage-org', qs], queryFn: () => api.get<OrgUsageReport>(`/api/usage?${qs}`), placeholderData: keepPreviousData });
  const r = q.data;
  if (q.isError) return <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700" role="alert">{q.error instanceof Error ? q.error.message : 'Could not load usage'}</div>;
  if (!r) return <div className="animate-pulse text-sm text-slate-400">Loading…</div>;
  return (
    <div className="space-y-4" data-testid="v2-usage-org">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm text-navy">{r.totals.calls ? `${tokensFull(r.totals.tokens)} tokens across ${r.byProject.length} projects.` : 'No model calls in this period.'}</p>
        <a href={`/api/usage/export.csv?${qs}`} download className="rounded-lg border border-slate-300 bg-white px-3 py-1 text-sm font-medium text-navy hover:bg-slate-100" data-testid="v2-usage-org-csv">Download CSV</a>
      </div>
      {r.unattributed.calls > 0 && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900" role="status" data-testid="v2-usage-unattributed">
          {tokensFull(r.unattributed.tokens)} tokens in {calls(r.unattributed.calls)} have no project or person recorded. Every call should have both; ask your developers to look at calls logged as “UNATTRIBUTED”.
        </div>
      )}
      <PoolsPanel />
      <Tiles t={r.totals} />
      <Days rows={fillDays(r.range, r.byDay)} />
      <Panel title="Projects" hint="Open a project to see who spent it" testid="v2-usage-projects">
        <Bars rows={r.byProject.map((p) => ({ key: p.projectId, label: p.name, value: p.tokens, note: `${p.people} ${p.people === 1 ? 'person' : 'people'} · ${calls(p.calls)} · ${percent(p.wasteShare)} unproductive` }))} onPick={(id) => { if (!r.byProject.find((p) => p.projectId === id)?.deleted) onOpenProject?.(id); }} />
      </Panel>
      <div className="grid gap-4 md:grid-cols-2">
        <Panel title="What it was spent on" hint="By kind of work" testid="v2-usage-org-activity"><Bars rows={r.byActivity.map((a) => ({ key: a.activity, label: activityLabel(a.activity), value: a.tokens, note: calls(a.calls) }))} /></Panel>
        <Panel title="By model" hint="Which model served the tokens" testid="v2-usage-org-model"><Bars rows={r.byModel.map((m) => ({ key: `${m.provider}/${m.model}`, label: m.model || m.provider, value: m.tokens, note: `${m.provider} · ${calls(m.calls)}` }))} /></Panel>
      </div>
      <Waste rows={r.waste} totals={r.totals} onPick={() => undefined} />
      <Reconciliation />
    </div>
  );
}

/** Is the ledger right? The nightly comparison with what AWS itself logged for the same calls. */
function Reconciliation() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['usage-recon'], queryFn: () => api.get<ReconList>('/api/usage/reconciliation?days=14') });
  const [day, setDay] = useState<string | null>(null);
  const [err, setErr] = useState('');
  const detail = useQuery({ queryKey: ['usage-recon', day], enabled: Boolean(day), queryFn: () => api.get<{ run: ReconRun; differences: ReconDiff[] }>(`/api/usage/reconciliation/${day}`) });
  const yesterday = new Date(Date.now() - 86_400_000).toISOString().slice(0, 10);
  const run = useMutation({
    mutationFn: () => api.post<{ status: string }>('/api/usage/reconciliation/run', { day: yesterday }),
    onSuccess: () => { setErr(''); void qc.invalidateQueries({ queryKey: ['usage-recon'] }); setDay(yesterday); },
    onError: (e) => setErr(e instanceof Error ? e.message : 'Could not run'),
  });
  const l = q.data;
  const drifted = l?.runs[0]?.status === 'drift';
  return (
    <Panel title="Checked against AWS" hint="Each night the ledger is compared, call by call, with Bedrock's own invocation log" testid="v2-usage-recon">
      {!l ? <div className="animate-pulse text-sm text-slate-400">Loading…</div> : (
        <>
          <p className={`mb-2 text-sm ${drifted ? 'font-medium text-red-700' : 'text-slate-700'}`} data-testid="v2-recon-headline" role={drifted ? 'alert' : undefined}>{reconHeadline(l)}</p>
          {!l.configured && <p className="text-xs text-slate-500">To turn it on, enable Bedrock model invocation logging and set <code>BEDROCK_RECON_SOURCE</code> (see docs/bedrock-reconciliation.md).</p>}
          {l.configured && (
            <>
              <div className="mb-2 flex items-center gap-2 text-sm">
                <button type="button" onClick={() => run.mutate()} disabled={run.isPending} className="rounded-lg border border-slate-300 bg-white px-3 py-1 font-medium text-navy hover:bg-slate-100 disabled:opacity-50" data-testid="v2-recon-run">{run.isPending ? 'Comparing…' : `Compare ${yesterday} now`}</button>
                <span className="text-xs text-slate-500">Source: {l.source}. Allowed difference {Math.round(l.threshold * 100)}%.</span>
                {err && <span className="text-xs text-red-700" role="alert">{err}</span>}
              </div>
              {l.runs.length > 0 && (
                <table className="w-full text-sm"><thead><tr className="text-left text-xs uppercase tracking-wide text-slate-500"><th className="py-1">Day</th><th>Result</th><th className="text-right">Ledger</th><th className="text-right">AWS</th><th className="text-right">Difference</th><th className="text-right">Calls that differ</th></tr></thead>
                  <tbody>{l.runs.map((r) => (
                    <tr key={r.day} className={`cursor-pointer border-t border-slate-200 hover:bg-slate-50 ${day === r.day ? 'bg-slate-50' : ''}`} onClick={() => setDay(day === r.day ? null : r.day)} data-testid="v2-recon-row">
                      <td className="py-1">{r.day}</td><td><Pill tone={RECON_STATUS[r.status].tone}>{RECON_STATUS[r.status].label}</Pill></td>
                      <td className="text-right tabular-nums">{tokensFull(r.ledgerTokens)}</td><td className="text-right tabular-nums">{tokensFull(r.awsTokens)}</td>
                      <td className="text-right tabular-nums">{driftLabel(r.drift)}</td><td className="text-right tabular-nums">{r.mismatched + r.missingInLedger + r.missingInAws}</td></tr>))}</tbody></table>
              )}
              {day && detail.data && <ReconDetail run={detail.data.run} diffs={detail.data.differences} />}
            </>
          )}
        </>
      )}
    </Panel>
  );
}

function ReconDetail({ run, diffs }: { run: ReconRun; diffs: ReconDiff[] }) {
  return (
    <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm" data-testid="v2-recon-detail">
      <p className="text-slate-700">{calls(run.matched)} match exactly; {run.mismatched} with different counts; {run.missingInLedger} in AWS only; {run.missingInAws} in the ledger only{run.unmatchableLedger ? `; ${run.unmatchableLedger} ledger rows carry no AWS request id` : ''}.</p>
      {run.note && <p className="mt-1 text-xs text-amber-800">{run.note}</p>}
      {run.error && <p className="mt-1 text-xs text-red-700">{run.error}</p>}
      {run.convention && <p className="mt-1 text-xs text-slate-500">AWS counts input {run.convention === 'input_includes_cache' ? 'including' : run.convention === 'input_excludes_cache' ? 'excluding' : 'in a way indistinguishable from the ledger with respect to'} cached tokens.</p>}
      {run.byModel.length > 0 && <ul className="mt-2 text-xs text-slate-600">{run.byModel.map((m) => <li key={m.model}>{m.model}: ledger {tokensFull(m.ledger)}, AWS {tokensFull(m.aws)} ({driftLabel(m.drift)})</li>)}</ul>}
      {diffs.length > 0 && (
        <table className="mt-2 w-full text-xs"><thead><tr className="text-left uppercase tracking-wide text-slate-500"><th className="py-1">What</th><th>AWS request id</th><th>Work</th><th className="text-right">Ledger</th><th className="text-right">AWS</th></tr></thead>
          <tbody>{diffs.slice(0, 25).map((d, i) => (
            <tr key={`${d.providerRequestId}-${i}`} className="border-t border-slate-200"><td className="py-1">{DIFF_LABEL[d.kind]}</td><td className="font-mono">{d.providerRequestId}</td><td>{d.activity ? activityLabel(d.activity) : ''}</td>
              <td className="text-right tabular-nums">{tokensFull(d.ledgerTokens)}</td><td className="text-right tabular-nums">{tokensFull(d.awsTokens)}</td></tr>))}</tbody></table>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------- pieces
function Tiles({ t, extra }: { t: UsageTotals; extra?: { label: string; value: string; sub: string } }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" data-testid="v2-usage-tiles">
      <Tile label="Tokens" value={tokensLabel(t.tokens)} sub={`${tokensFull(t.tokens)} · ${calls(t.calls)}`} hero testid="v2-usage-total" />
      {extra ? <Tile label={extra.label} value={extra.value} sub={extra.sub} /> : <Tile label="Input and output" value={`${tokensLabel(t.inputTokens + t.cacheReadTokens + t.cacheWriteTokens)} in`} sub={`${tokensLabel(t.outputTokens)} out`} />}
      <Tile label="Input served from cache" value={percent(t.cacheReadShare)} sub="Cached input is reused, not re-read from scratch" />
      <Tile label="Unproductive" value={percent(t.wasteShare)} sub={`${tokensLabel(t.wasteTokens)} on failed, refused or cut-off calls`} />
    </div>
  );
}

function Tile({ label, value, sub, hero, testid }: { label: string; value: string; sub: string; hero?: boolean; testid?: string }) {
  return (
    <div className="rounded-xl border border-slate-300 bg-white p-4">
      <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`font-display font-bold text-navy ${hero ? 'text-4xl' : 'text-2xl'}`} data-testid={testid}>{value}</div>
      <div className="text-xs text-slate-500">{sub}</div>
    </div>
  );
}

/** Daily columns: one series, one hue; the exact figure for the day under the pointer is read out below, and every column is a button for keyboard use. */
function Days({ rows }: { rows: Array<{ day: string; tokens: number; calls: number }> }) {
  const [hover, setHover] = useState<number | null>(null);
  const widths = barWidths(rows.map((r) => r.tokens));
  const shown = hover !== null ? rows[hover] : null;
  return (
    <section className="rounded-xl border border-slate-300 bg-white p-4" data-testid="v2-usage-days">
      <div className="mb-2 flex items-baseline justify-between"><h2 className="font-display text-base font-bold text-navy">Tokens per day</h2>
        <span className="text-xs text-slate-500" aria-live="polite" data-testid="v2-usage-day-readout">{shown ? `${new Date(`${shown.day}T00:00:00Z`).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' })}: ${tokensFull(shown.tokens)} tokens, ${calls(shown.calls)}` : 'Point at a day for its figure'}</span></div>
      <div className="flex h-32 items-end gap-[2px]" onMouseLeave={() => setHover(null)} role="list">
        {rows.map((r, i) => (
          <button key={r.day} type="button" role="listitem" aria-label={`${r.day}: ${tokensFull(r.tokens)} tokens`} onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} onBlur={() => setHover(null)}
            className="group flex h-full min-w-0 flex-1 items-end focus:outline-none">
            <span className={`block w-full rounded-t-[3px] ${hover === i ? 'bg-brand-600' : 'bg-brand-500'} ${r.tokens ? '' : 'bg-slate-200'} group-focus-visible:ring-2 group-focus-visible:ring-brand-600`} style={{ height: `${r.tokens ? Math.max(widths[i] ?? 0, 3) : 2}%` }} />
          </button>
        ))}
      </div>
      <div className="mt-1 flex justify-between text-[11px] text-slate-500"><span>{rows[0]?.day}</span><span>{rows[rows.length - 1]?.day}</span></div>
    </section>
  );
}

interface BarRow { key: string; label: string; value: number; note?: string; strong?: boolean }
function Bars({ rows, onPick }: { rows: BarRow[]; onPick?: (key: string) => void }) {
  const widths = barWidths(rows.map((r) => r.value));
  if (rows.length === 0) return <p className="text-sm text-slate-500">Nothing in this period.</p>;
  return (
    <ul className="space-y-2">
      {rows.map((r, i) => {
        const inner = (
          <>
            <div className="flex items-baseline justify-between gap-2 text-sm"><span className={`truncate text-navy ${r.strong ? 'font-semibold' : ''}`}>{r.label}</span><span className="shrink-0 tabular-nums text-navy">{tokensFull(r.value)}</span></div>
            <div className="mt-0.5 h-2 rounded-full bg-slate-100"><div className={`h-2 rounded-full ${r.strong ? 'bg-brand-600' : 'bg-brand-500'}`} style={{ width: `${widths[i]}%` }} /></div>
            {r.note && <div className="mt-0.5 text-xs text-slate-500">{r.note}</div>}
          </>
        );
        return <li key={r.key}>{onPick ? <button type="button" className="block w-full rounded-md text-left hover:bg-slate-50" onClick={() => onPick(r.key)}>{inner}</button> : inner}</li>;
      })}
    </ul>
  );
}

function Panel({ title, hint, children, testid }: { title: string; hint?: string; children: ReactNode; testid?: string }) {
  return (
    <section className="rounded-xl border border-slate-300 bg-white p-4" data-testid={testid}>
      <h2 className="font-display text-base font-bold text-navy">{title}</h2>
      {hint && <p className="mb-3 text-xs text-slate-500">{hint}</p>}
      {children}
    </section>
  );
}
