import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../api/client';
import { filterStarters, ROLE_LABEL, spentPercent, starterStages, statusChip, STAGE_NAMES, tokensLabel, WIRE_TONE, type Card, type CoreCard, type DefDetail, type DefKind, type PipelineView, type ProjectUsage, type RunHistoryItem, type Rights } from '../../lib/agents';
import type { ProjectMember } from '../../api/types';
import { Pill } from '../bits';
import AgentBuilder from './AgentBuilder';
import AgentRunPanel from './AgentRunPanel';
import StarterView from './StarterView';
import ApprovalsView from './ApprovalsView';

const btn = 'rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:border-brand-400 disabled:opacity-50';
const primary = 'rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-50';
type ListResp = { rights: Rights; mine: Card[]; open: Card[]; pending: number };

/** Project Context -> Agents and skills: what this project built, the organisation's open library, and who decides. */
export default function AgentsTab({ projectId, userId, onBuilder }: { projectId: string; userId: string; onBuilder?: (open: boolean) => void }) {
  const qc = useQueryClient();
  const [kind, setKind] = useState<DefKind>('agent');
  const [view, setView] = useState<'mine' | 'open' | 'approvals' | 'people' | 'usage' | 'runs' | 'pipeline'>('mine');
  const [editing, setEditing] = useState<string | null>(null);
  const [running, setRunning] = useState<string | null>(null);
  const [starter, setStarter] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [stageFilter, setStageFilter] = useState(0);
  const [msg, setMsg] = useState('');
  const q = useQuery({ queryKey: ['agent-lists', projectId, kind], queryFn: () => api.get<ListResp>(`/api/projects/${projectId}/agents?kind=${kind}`) });
  const open = (id: string | null) => { setEditing(id); onBuilder?.(id !== null); };
  const starters = useQuery({ queryKey: ['agent-starters', projectId, kind], queryFn: () => api.get<{ items: CoreCard[] }>(`/api/projects/${projectId}/agent-starters?kind=${kind}`), enabled: view === 'open' });
  const create = useMutation({
    mutationFn: () => api.post<DefDetail>('/api/agent-defs', { kind, name: kind === 'agent' ? 'New agent' : 'New skill', scope: 'project', projectId }),
    onSuccess: (d) => { void qc.invalidateQueries({ queryKey: ['agent-lists'] }); open(d.def.id); }, onError: (e) => setMsg(e instanceof Error ? e.message : 'Could not create it'),
  });
  const copyStarter = useMutation({
    mutationFn: (id: string) => api.post<DefDetail>('/api/agent-defs/fork', { sourceKind: 'core', sourceId: id, kind, scope: 'project', projectId }),
    onSuccess: (d) => { void qc.invalidateQueries({ queryKey: ['agent-lists'] }); setView('mine'); open(d.def.id); }, onError: (e) => setMsg(e instanceof Error ? e.message : 'Could not copy it'),
  });
  const copy = useMutation({
    mutationFn: (c: Card) => api.post<DefDetail>('/api/agent-defs/fork', { sourceKind: 'def', sourceId: c.id, kind: c.kind, scope: 'project', projectId }),
    onSuccess: (d) => { void qc.invalidateQueries({ queryKey: ['agent-lists'] }); setView('mine'); open(d.def.id); }, onError: (e) => setMsg(e instanceof Error ? e.message : 'Could not copy it'),
  });
  if (starter && !editing) return <StarterView projectId={projectId} kind={kind} coreId={starter} canCopy={Boolean(q.data?.rights.edit)} onBack={() => setStarter(null)} onCopied={(id) => { setStarter(null); setView('mine'); void qc.invalidateQueries({ queryKey: ['agent-lists'] }); open(id); }} />;
  if (running) return <AgentRunPanel projectId={projectId} defId={running} onBack={() => setRunning(null)} />;
  if (editing) return <AgentBuilder defId={editing} projectId={projectId} onBack={() => open(null)} />;
  const rights = q.data?.rights;
  const views: Array<['mine' | 'open' | 'approvals' | 'people' | 'usage' | 'runs' | 'pipeline', string]> = [['mine', 'Mine'], ['open', 'Library'], ['runs', 'Runs'], ['pipeline', 'Pipeline'], ...(rights?.approve ? [['approvals', `Approvals${q.data?.pending ? ` (${q.data.pending})` : ''}`] as ['approvals', string]] : []), ...(rights?.edit || rights?.approve || rights?.manage ? [['usage', 'Usage'] as ['usage', string]] : []), ...(rights?.manage ? [['people', 'Who can edit and approve'] as ['people', string]] : [])];
  const cards = q.data?.mine ?? [];
  return (
    <div data-testid="v2-agents-tab">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex gap-1" role="group" aria-label="Agents or skills">{(['agent', 'skill'] as const).map((k) => <button key={k} type="button" aria-pressed={kind === k} data-testid={`v2-agents-kind-${k}`} onClick={() => { setKind(k); setQuery(''); setStageFilter(0); }} className={`rounded-lg px-3 py-1 text-xs font-semibold ${kind === k ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-600'}`}>{k === 'agent' ? 'Agents' : 'Skills'}</button>)}</div>
        <div className="flex gap-1" role="tablist" aria-label="Library view">{views.map(([id, l]) => <button key={id} role="tab" aria-selected={view === id} data-testid={`v2-agents-view-${id}`} onClick={() => setView(id)} className={`rounded-lg px-3 py-1 text-xs font-semibold ${view === id ? 'bg-navy text-white' : 'bg-slate-100 text-slate-600'}`}>{l}</button>)}</div>
        {rights?.edit ? <button type="button" className={primary} disabled={create.isPending} data-testid="v2-agents-new" onClick={() => create.mutate()}>+ New {kind}</button> : <span className="text-xs text-slate-500">Ask the project manager for permission to build agents.</span>}
      </div>
      {msg && <div className="mb-2 rounded-lg bg-slate-100 px-3 py-1.5 text-xs text-slate-700" role="status">{msg}</div>}
      {view === 'approvals' && <ApprovalsView projectId={projectId} userId={userId} onOpen={(id) => open(id)} />}
      {view === 'people' && <People projectId={projectId} />}
      {view === 'usage' && <Usage projectId={projectId} />}
      {view === 'runs' && <Runs projectId={projectId} onOpen={(id) => setRunning(id)} />}
      {view === 'pipeline' && <Pipeline projectId={projectId} />}
      {view === 'open' && <Library kind={kind} org={q.data?.open ?? []} starters={starters.data?.items ?? []} loading={starters.isLoading} query={query} setQuery={setQuery} stage={stageFilter} setStage={setStageFilter} canCopy={Boolean(rights?.edit)} copyPending={copy.isPending}
        onView={(id) => setStarter(id)} onOpenOrg={(id) => open(id)} onRunOrg={(id) => setRunning(id)} onCopyOrg={(c) => copy.mutate(c)} onCopyStarter={(id) => copyStarter.mutate(id)} />}
      {view === 'mine' && (
        q.isLoading ? <div className="animate-pulse text-sm text-slate-400">Loading…</div> :
        !cards.length ? <div className="rounded-xl border border-slate-300 bg-white p-6 text-center text-sm text-slate-500" data-testid="v2-agents-empty">No {kind}s of your own yet. Start from a built-in or shared one in the <button type="button" className="font-semibold text-brand-700 underline" data-testid="v2-agents-browse" onClick={() => setView('open')}>Library</button>, or press + New {kind}.</div> :
        <ul className="grid gap-3 md:grid-cols-2" data-testid="v2-agents-list">
          {cards.map((c) => {
            const chip = statusChip(c.status, c.version);
            return (
              <li key={c.id} className="rounded-xl border border-slate-300 bg-white p-4" data-testid="v2-agent-card">
                <div className="flex items-start justify-between gap-2"><div className="font-display text-base font-bold text-navy">{c.name}</div>{view === 'mine' ? <Pill tone={chip.tone}>{chip.label}</Pill> : <Pill tone="amber">Open · v{c.publishedVersion}</Pill>}</div>
                <p className="mt-1 text-sm text-slate-600">{c.description || 'No description yet.'}</p>
                <div className="mt-2 flex flex-wrap gap-1 text-xs"><Pill>{c.roleLabel}</Pill>{c.inputs.map((i) => <Pill key={i} tone="brand">● {i}</Pill>)}{c.outputs.map((o) => <Pill key={o} tone="green">{o} ●</Pill>)}{c.children > 0 && <Pill tone="amber">↳ {c.children}</Pill>}</div>
                <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                  {view === 'mine' ? <><button type="button" className={btn} onClick={() => open(c.id)} data-testid="v2-agent-open">{rights?.edit ? 'Open' : 'View'}</button>{c.publishedVersion && <button type="button" className={primary} data-testid="v2-agent-run" onClick={() => setRunning(c.id)}>▶ Run</button>}<span>{c.usedInStages ? `Used in ${c.usedInStages} stage${c.usedInStages > 1 ? 's' : ''}` : 'Not used in a stage yet'}</span></>
                    : <><button type="button" className={btn} onClick={() => open(c.id)}>View</button><button type="button" className={btn} data-testid="v2-agent-run" onClick={() => setRunning(c.id)}>▶ Run</button>{rights?.edit && <button type="button" className={primary} disabled={copy.isPending} data-testid="v2-agent-copy" onClick={() => copy.mutate(c)}>Copy to project</button>}<span>A copy is yours to change; it does not follow the original.</span></>}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function People({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const g = useQuery({ queryKey: ['agent-grants', projectId], queryFn: () => api.get<{ grants: Array<{ userId: string; name: string; canEdit: boolean; canApprove: boolean }> }>(`/api/projects/${projectId}/agent-grants`) });
  const members = useQuery({ queryKey: ['members', projectId], queryFn: () => api.get<{ members: ProjectMember[] }>(`/api/projects/${projectId}/members`) });
  const [err, setErr] = useState('');
  const set = useMutation({
    mutationFn: (v: { userId: string; canEdit: boolean; canApprove: boolean }) => api.put(`/api/projects/${projectId}/agent-grants`, v),
    onSuccess: () => { setErr(''); void qc.invalidateQueries({ queryKey: ['agent-grants', projectId] }); void qc.invalidateQueries({ queryKey: ['agent-lists'] }); }, onError: (e) => setErr(e instanceof Error ? e.message : 'Could not save'),
  });
  const by = new Map((g.data?.grants ?? []).map((x) => [x.userId, x]));
  return (
    <div className="rounded-xl border border-slate-300 bg-white p-4" data-testid="v2-agent-people">
      <p className="mb-2 text-sm text-slate-600">The project manager can always build and approve. Give a team member the right to edit, or to approve other people's work. Nobody approves their own submission.</p>
      {err && <div className="mb-2 text-xs text-bared-700" role="alert">{err}</div>}
      <table className="w-full text-sm"><thead><tr className="text-left text-xs text-slate-500"><th>Person</th><th>Can edit</th><th>Can approve</th></tr></thead><tbody>
        {(members.data?.members ?? []).map((m) => { const x = by.get(m.userId); return (
          <tr key={m.userId} className="border-t border-slate-100"><td className="py-1.5">{m.displayName || m.email}<span className="ml-2 text-xs text-slate-400">{m.role}</span></td>
            <td><input type="checkbox" aria-label={`${m.displayName || m.email} can edit`} checked={Boolean(x?.canEdit)} onChange={(e) => set.mutate({ userId: m.userId, canEdit: e.target.checked, canApprove: Boolean(x?.canApprove) && e.target.checked })} /></td>
            <td><input type="checkbox" aria-label={`${m.displayName || m.email} can approve`} checked={Boolean(x?.canApprove)} onChange={(e) => set.mutate({ userId: m.userId, canEdit: Boolean(x?.canEdit) || e.target.checked, canApprove: e.target.checked })} /></td></tr>); })}
      </tbody></table>
    </div>
  );
}

function Usage({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['agent-project-usage', projectId], queryFn: () => api.get<ProjectUsage>(`/api/projects/${projectId}/agent-usage`) });
  const [text, setText] = useState<string | null>(null);
  const [err, setErr] = useState('');
  const save = useMutation({
    mutationFn: (monthlyTokens: number | null) => api.put<ProjectUsage>(`/api/projects/${projectId}/agent-limits`, { monthlyTokens }),
    onSuccess: (d) => { setErr(''); setText(null); qc.setQueryData(['agent-project-usage', projectId], d); }, onError: (e) => setErr(e instanceof Error ? e.message : 'Could not save'),
  });
  const u = q.data;
  if (!u) return <div className="animate-pulse text-sm text-slate-400">Loading…</div>;
  const pct = spentPercent(u.used, u.limit);
  const shown = text ?? (u.limit === null ? '' : String(u.limit));
  const apply = () => { const n = shown.trim() === '' ? null : Number(shown.replace(/[,_\s]/g, '')); if (n !== null && (!Number.isInteger(n) || n < 0)) { setErr('Use a whole number of tokens, or leave it empty for no limit'); return; } save.mutate(n); };
  return (
    <div className="rounded-xl border border-slate-300 bg-white p-4" data-testid="v2-agent-usage">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Custom agent tokens, {u.month}</div>
          <div className="font-display text-2xl font-bold text-navy" data-testid="v2-usage-used">{tokensLabel(u.used)}{u.limit !== null && <span className="text-base font-normal text-slate-500"> of {tokensLabel(u.limit)}</span>}</div>
          <div className="text-xs text-slate-500">{u.runs} run{u.runs === 1 ? '' : 's'} this month. Tokens are counted, not money: the price depends on the models your administrator routes to.</div></div>
        {u.rights.manage ? (
          <div className="flex items-end gap-2"><label className="text-xs font-semibold text-slate-600">Monthly budget (tokens)
            <input data-testid="v2-usage-limit" inputMode="numeric" className="mt-1 block w-40 rounded-lg border border-slate-300 px-2 py-1 text-sm font-normal" value={shown} placeholder="No limit" onChange={(e) => setText(e.target.value)} /></label>
            <button type="button" className={primary} disabled={save.isPending} data-testid="v2-usage-save" onClick={apply}>Save</button></div>
        ) : <div className="text-xs text-slate-500">{u.limit === null ? 'No monthly budget is set.' : 'The project manager sets the budget.'}</div>}
      </div>
      {err && <div className="mt-2 text-xs text-bared-700" role="alert">{err}</div>}
      {u.limit !== null && <div className="mt-3"><div className="h-2 overflow-hidden rounded-full bg-slate-200" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}><div className={`h-full ${u.exceeded ? 'bg-bared-500' : pct >= 80 ? 'bg-amber-500' : 'bg-brand-500'}`} style={{ width: `${pct}%` }} /></div>
        {u.exceeded && <div className="mt-1 text-xs text-bared-700" role="status" data-testid="v2-usage-exceeded">The budget is spent. Custom agents will not run until the budget is raised or the month ends.</div>}</div>}
      <table className="mt-4 w-full text-sm"><thead><tr className="text-left text-xs text-slate-500"><th>Agent or skill</th><th>Runs</th><th className="text-right">Tokens</th></tr></thead><tbody>
        {u.byAgent.map((a) => <tr key={a.defId} className="border-t border-slate-100" data-testid="v2-usage-row"><td className="py-1.5">{a.name} <span className="text-xs text-slate-400">{a.kind}</span></td><td>{a.runs}</td><td className="text-right">{a.tokens.toLocaleString()}</td></tr>)}
        {!u.byAgent.length && <tr><td colSpan={3} className="py-3 text-center text-slate-400">Nothing has run this month.</td></tr>}
      </tbody></table>
    </div>
  );
}

function Runs({ projectId, onOpen }: { projectId: string; onOpen: (defId: string) => void }) {
  const q = useQuery({ queryKey: ['agent-run-history', projectId, 'all'], queryFn: () => api.get<{ runs: RunHistoryItem[] }>(`/api/projects/${projectId}/agent-runs?limit=30`) });
  const runs = q.data?.runs ?? [];
  return (
    <div className="rounded-xl border border-slate-300 bg-white p-4" data-testid="v2-agent-runs">
      <p className="mb-2 text-sm text-slate-600">Runs people started on request, on their own or inside a stage. A stage's automatic runs are recorded on the artefacts they wrote.</p>
      {q.isLoading ? <div className="animate-pulse text-sm text-slate-400">Loading…</div> : !runs.length ? <div className="py-4 text-center text-sm text-slate-400">Nothing has been run yet.</div> : (
        <table className="w-full text-sm"><thead><tr className="text-left text-xs text-slate-500"><th>Agent</th><th>By</th><th>When</th><th>Where</th><th className="text-right">Tokens</th><th /></tr></thead><tbody>
          {runs.map((r) => <tr key={r.id} className="border-t border-slate-100" data-testid="v2-runs-row"><td className="py-1.5">{r.name} <span className="text-xs text-slate-400">v{r.version}</span></td><td>{r.by}</td><td>{new Date(r.at).toLocaleString()}</td>
            <td>{r.stage ? `Saved to ${r.stage}` : 'On its own'}</td><td className="text-right">{r.tokens.toLocaleString()}</td><td className="text-right"><button type="button" className={btn} onClick={() => onOpen(r.defId)}>Run again</button></td></tr>)}
        </tbody></table>)}
    </div>
  );
}

function Pipeline({ projectId }: { projectId: string }) {
  const q = useQuery({ queryKey: ['agent-pipeline', projectId], queryFn: () => api.get<PipelineView>(`/api/projects/${projectId}/agent-pipeline`) });
  const p = q.data;
  if (!p) return <div className="animate-pulse text-sm text-slate-400">Loading…</div>;
  return (
    <div className="space-y-3" data-testid="v2-agent-pipeline">
      <p className="text-sm text-slate-600">The workflow, stage by stage, with the agents attached to each in the order they run. A red input means nothing earlier produces it, so the agent would be skipped. Attach, reorder and remove agents in the Workflow designer.</p>
      {p.problems.length > 0 && <div className="rounded-lg bg-bared-200 px-3 py-2 text-xs text-bared-700" role="alert" data-testid="v2-pipeline-problems">{p.problems.map((x) => `${x.agent} in ${x.stage} needs ${x.inputs.map((i) => i.name).join(', ')}`).join('. ')}.</div>}
      <ol className="space-y-2">
        {p.stages.map((s) => (
          <li key={s.key} className="rounded-xl border border-slate-300 bg-white p-3" data-testid="v2-pipeline-stage">
            <div className="flex flex-wrap items-center gap-2"><span className="font-display text-sm font-bold text-navy">{s.seq}. {s.name}</span>{s.agentsOnly && <Pill tone="amber">Built from agents</Pill>}<span className="text-xs text-slate-400">writes {s.outputs.join(', ') || 'nothing declared'}</span></div>
            {s.agents.length === 0 ? <div className="mt-1 text-xs text-slate-400">No custom agents.</div> : (
              <ol className="mt-2 space-y-1.5 border-l-2 border-slate-200 pl-3">{s.agents.map((a, n) => (
                <li key={a.defId} className="text-sm" data-testid="v2-pipeline-agent"><b>{n + 1}. {a.name}</b> <Pill tone={a.runs === 'on_request' ? 'amber' : 'green'}>{a.runs === 'always' ? 'every time' : a.runs === 'when' ? 'when…' : 'on request'}</Pill>
                  <div className="mt-0.5 flex flex-wrap gap-1 text-xs">{a.inputs.map((i) => <Pill key={i.name} tone={WIRE_TONE[i.status]} title={i.from}>{i.name} ← {i.from}</Pill>)}<span className="text-slate-400">→ {a.outputs.join(', ') || 'nothing'}</span></div></li>))}</ol>)}
          </li>))}
      </ol>
    </div>
  );
}

function Library({ kind, org, starters, loading, query, setQuery, stage, setStage, canCopy, copyPending, onView, onOpenOrg, onRunOrg, onCopyOrg, onCopyStarter }: {
  kind: DefKind; org: Card[]; starters: CoreCard[]; loading: boolean; query: string; setQuery: (v: string) => void; stage: number; setStage: (n: number) => void; canCopy: boolean; copyPending: boolean;
  onView: (id: string) => void; onOpenOrg: (id: string) => void; onRunOrg: (id: string) => void; onCopyOrg: (c: Card) => void; onCopyStarter: (id: string) => void;
}) {
  const shown = filterStarters(starters, query, stage);
  const stages = starterStages(starters);
  return (
    <div className="space-y-5" data-testid="v2-agent-library-view">
      {org.length > 0 && (
        <section data-testid="v2-lib-shared">
          <h3 className="font-display text-base font-bold text-navy">Shared by your organisation</h3>
          <p className="mb-2 text-xs text-slate-500">Approved by an administrator and open to every project. A copy is yours to change.</p>
          <ul className="grid gap-3 md:grid-cols-2">{org.map((c) => (
            <li key={c.id} className="rounded-xl border border-slate-300 bg-white p-4" data-testid="v2-agent-card">
              <div className="flex items-start justify-between gap-2"><div className="font-display text-base font-bold text-navy">{c.name}</div><Pill tone="amber">Shared · v{c.publishedVersion}</Pill></div>
              <p className="mt-1 text-sm text-slate-600">{c.description || 'No description yet.'}</p>
              <div className="mt-2 flex flex-wrap gap-1 text-xs"><Pill>{c.roleLabel}</Pill>{c.inputs.map((i) => <Pill key={i} tone="brand">● {i}</Pill>)}{c.outputs.map((o) => <Pill key={o} tone="green">{o} ●</Pill>)}</div>
              <div className="mt-3 flex flex-wrap items-center gap-2"><button type="button" className={btn} onClick={() => onOpenOrg(c.id)}>View</button><button type="button" className={btn} data-testid="v2-agent-run" onClick={() => onRunOrg(c.id)}>▶ Run</button>
                {canCopy && <button type="button" className={primary} disabled={copyPending} data-testid="v2-agent-copy" onClick={() => onCopyOrg(c)}>Copy to project</button>}</div>
            </li>))}</ul>
        </section>)}
      <section data-testid="v2-lib-builtin">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div><h3 className="font-display text-base font-bold text-navy">Built-in {kind === 'agent' ? 'agents' : 'skills'} you can copy</h3>
            <p className="text-xs text-slate-500">{kind === 'agent' ? 'The specialists that write each stage\'s documents.' : 'Instruction-only skills.'} Open one to read its definition, or copy it into this project and extend it for your own case.</p></div>
          <input aria-label="Search the library" data-testid="v2-lib-search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search by name or what it writes" className="w-64 rounded-lg border border-slate-300 px-2 py-1 text-sm" />
        </div>
        {stages.length > 1 && <div className="mt-2 flex flex-wrap gap-1.5" role="group" aria-label="Stage">{[0, ...stages].map((n) => <button key={n} type="button" aria-pressed={stage === n} onClick={() => setStage(n)} className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${stage === n ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>{n === 0 ? `All (${starters.length})` : STAGE_NAMES[n] ?? `Stage ${n}`}</button>)}</div>}
        {loading ? <div className="mt-3 animate-pulse text-sm text-slate-400">Loading…</div> : !shown.length ? (
          <div className="mt-3 rounded-xl border border-slate-300 bg-white p-6 text-center text-sm text-slate-500" data-testid="v2-lib-none">{starters.length ? 'Nothing matches that search.' : `No built-in ${kind}s are shared with projects. An administrator can share them under Governance → Agent library.`}</div>
        ) : (
          <ul className="mt-3 grid gap-3 md:grid-cols-2" data-testid="v2-lib-starters">{shown.map((c) => (
            <li key={c.id} className="rounded-xl border border-slate-300 bg-white p-4" data-testid="v2-starter-card">
              <div className="flex items-start justify-between gap-2"><div className="font-display text-base font-bold text-navy">{c.name}</div><Pill>🔒 Built-in</Pill></div>
              <p className="mt-1 text-sm text-slate-600">{c.description}</p>
              <div className="mt-2 flex flex-wrap gap-1 text-xs"><Pill>{ROLE_LABEL[c.role] ?? c.role}</Pill>{c.stage != null && <Pill tone="brand">{STAGE_NAMES[c.stage] ?? `Stage ${c.stage}`}</Pill>}{c.outputs.slice(0, 3).map((o) => <Pill key={o} tone="green">{o} ●</Pill>)}</div>
              <div className="mt-3 flex flex-wrap items-center gap-2"><button type="button" className={btn} data-testid="v2-starter-open" onClick={() => onView(c.id)}>View definition</button>
                {canCopy && <button type="button" className={primary} disabled={copyPending} data-testid="v2-starter-quick-copy" onClick={() => onCopyStarter(c.id)}>Copy to project</button>}</div>
            </li>))}</ul>)}
      </section>
    </div>
  );
}
