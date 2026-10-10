import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../api/client';
import { spentPercent, statusChip, tokensLabel, type Card, type DefDetail, type DefKind, type ProjectUsage, type Rights } from '../../lib/agents';
import type { ProjectMember } from '../../api/types';
import { Pill } from '../bits';
import AgentBuilder from './AgentBuilder';
import ApprovalsView from './ApprovalsView';

const btn = 'rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:border-brand-400 disabled:opacity-50';
const primary = 'rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-50';
type ListResp = { rights: Rights; mine: Card[]; open: Card[]; pending: number };

/** Project Context -> Agents and skills: what this project built, the organisation's open library, and who decides. */
export default function AgentsTab({ projectId, userId, onBuilder }: { projectId: string; userId: string; onBuilder?: (open: boolean) => void }) {
  const qc = useQueryClient();
  const [kind, setKind] = useState<DefKind>('agent');
  const [view, setView] = useState<'mine' | 'open' | 'approvals' | 'people' | 'usage'>('mine');
  const [editing, setEditing] = useState<string | null>(null);
  const [msg, setMsg] = useState('');
  const q = useQuery({ queryKey: ['agent-lists', projectId, kind], queryFn: () => api.get<ListResp>(`/api/projects/${projectId}/agents?kind=${kind}`) });
  const open = (id: string | null) => { setEditing(id); onBuilder?.(id !== null); };
  const create = useMutation({
    mutationFn: () => api.post<DefDetail>('/api/agent-defs', { kind, name: kind === 'agent' ? 'New agent' : 'New skill', scope: 'project', projectId }),
    onSuccess: (d) => { void qc.invalidateQueries({ queryKey: ['agent-lists'] }); open(d.def.id); }, onError: (e) => setMsg(e instanceof Error ? e.message : 'Could not create it'),
  });
  const copy = useMutation({
    mutationFn: (c: Card) => api.post<DefDetail>('/api/agent-defs/fork', { sourceKind: 'def', sourceId: c.id, kind: c.kind, scope: 'project', projectId }),
    onSuccess: (d) => { void qc.invalidateQueries({ queryKey: ['agent-lists'] }); setView('mine'); open(d.def.id); }, onError: (e) => setMsg(e instanceof Error ? e.message : 'Could not copy it'),
  });
  if (editing) return <AgentBuilder defId={editing} projectId={projectId} onBack={() => open(null)} />;
  const rights = q.data?.rights;
  const views: Array<['mine' | 'open' | 'approvals' | 'people' | 'usage', string]> = [['mine', 'Mine'], ['open', 'Open library'], ...(rights?.approve ? [['approvals', `Approvals${q.data?.pending ? ` (${q.data.pending})` : ''}`] as ['approvals', string]] : []), ...(rights?.edit || rights?.approve || rights?.manage ? [['usage', 'Usage'] as ['usage', string]] : []), ...(rights?.manage ? [['people', 'Who can edit and approve'] as ['people', string]] : [])];
  const cards = view === 'mine' ? q.data?.mine ?? [] : q.data?.open ?? [];
  return (
    <div data-testid="v2-agents-tab">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex gap-1" role="group" aria-label="Agents or skills">{(['agent', 'skill'] as const).map((k) => <button key={k} type="button" aria-pressed={kind === k} data-testid={`v2-agents-kind-${k}`} onClick={() => setKind(k)} className={`rounded-lg px-3 py-1 text-xs font-semibold ${kind === k ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-600'}`}>{k === 'agent' ? 'Agents' : 'Skills'}</button>)}</div>
        <div className="flex gap-1" role="tablist" aria-label="Library view">{views.map(([id, l]) => <button key={id} role="tab" aria-selected={view === id} data-testid={`v2-agents-view-${id}`} onClick={() => setView(id)} className={`rounded-lg px-3 py-1 text-xs font-semibold ${view === id ? 'bg-navy text-white' : 'bg-slate-100 text-slate-600'}`}>{l}</button>)}</div>
        {rights?.edit ? <button type="button" className={primary} disabled={create.isPending} data-testid="v2-agents-new" onClick={() => create.mutate()}>+ New {kind}</button> : <span className="text-xs text-slate-500">Ask the project manager for permission to build agents.</span>}
      </div>
      {msg && <div className="mb-2 rounded-lg bg-slate-100 px-3 py-1.5 text-xs text-slate-700" role="status">{msg}</div>}
      {view === 'approvals' && <ApprovalsView projectId={projectId} userId={userId} onOpen={(id) => open(id)} />}
      {view === 'people' && <People projectId={projectId} />}
      {view === 'usage' && <Usage projectId={projectId} />}
      {(view === 'mine' || view === 'open') && (
        q.isLoading ? <div className="animate-pulse text-sm text-slate-400">Loading…</div> :
        !cards.length ? <div className="rounded-xl border border-slate-300 bg-white p-6 text-center text-sm text-slate-500" data-testid="v2-agents-empty">{view === 'mine' ? `No ${kind}s built for this project yet.` : `The organisation has not opened any ${kind}s to projects yet.`}</div> :
        <ul className="grid gap-3 md:grid-cols-2" data-testid="v2-agents-list">
          {cards.map((c) => {
            const chip = statusChip(c.status, c.version);
            return (
              <li key={c.id} className="rounded-xl border border-slate-300 bg-white p-4" data-testid="v2-agent-card">
                <div className="flex items-start justify-between gap-2"><div className="font-display text-base font-bold text-navy">{c.name}</div>{view === 'mine' ? <Pill tone={chip.tone}>{chip.label}</Pill> : <Pill tone="amber">Open · v{c.publishedVersion}</Pill>}</div>
                <p className="mt-1 text-sm text-slate-600">{c.description || 'No description yet.'}</p>
                <div className="mt-2 flex flex-wrap gap-1 text-xs"><Pill>{c.roleLabel}</Pill>{c.inputs.map((i) => <Pill key={i} tone="brand">● {i}</Pill>)}{c.outputs.map((o) => <Pill key={o} tone="green">{o} ●</Pill>)}{c.children > 0 && <Pill tone="amber">↳ {c.children}</Pill>}</div>
                <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                  {view === 'mine' ? <><button type="button" className={btn} onClick={() => open(c.id)} data-testid="v2-agent-open">{rights?.edit ? 'Open' : 'View'}</button><span>{c.usedInStages ? `Used in ${c.usedInStages} stage${c.usedInStages > 1 ? 's' : ''}` : 'Not used in a stage yet'}</span></>
                    : <><button type="button" className={btn} onClick={() => open(c.id)}>View</button>{rights?.edit && <button type="button" className={primary} disabled={copy.isPending} data-testid="v2-agent-copy" onClick={() => copy.mutate(c)}>Copy to project</button>}<span>A copy is yours to change; it does not follow the original.</span></>}
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
