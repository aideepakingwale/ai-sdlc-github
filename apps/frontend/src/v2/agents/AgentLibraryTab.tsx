import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../api/client';
import { statusChip, type Card, type CoreCard, type DefDetail, type DefKind, type Guardrail } from '../../lib/agents';
import { Pill } from '../bits';
import AgentBuilder from './AgentBuilder';
import ApprovalsView from './ApprovalsView';

const btn = 'rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:border-brand-400 disabled:opacity-50';
const primary = 'rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-50';

/** Governance -> Agent library (super-admin): the built-in agents and skills (locked), the organisation's own, the guardrails and approvals. */
export default function AgentLibraryTab({ userId, onBuilder }: { userId: string; onBuilder?: (open: boolean) => void }) {
  const qc = useQueryClient();
  const [kind, setKind] = useState<DefKind>('agent');
  const [view, setView] = useState<'org' | 'core' | 'guardrails' | 'approvals'>('org');
  const [editing, setEditing] = useState<string | null>(null);
  const [core, setCore] = useState<{ id: string; kind: DefKind } | null>(null);
  const [msg, setMsg] = useState('');
  const open = (id: string | null) => { setEditing(id); onBuilder?.(id !== null); };
  const lib = useQuery({ queryKey: ['agent-lists', 'org', kind], queryFn: () => api.get<{ core: CoreCard[]; org: Card[]; projectOwned: number }>(`/api/agent-library?kind=${kind}`) });
  const create = useMutation({
    mutationFn: () => api.post<DefDetail>('/api/agent-defs', { kind, name: kind === 'agent' ? 'New organisation agent' : 'New organisation skill', scope: 'org' }),
    onSuccess: (d) => { void qc.invalidateQueries({ queryKey: ['agent-lists'] }); open(d.def.id); }, onError: (e) => setMsg(e instanceof Error ? e.message : 'Could not create it'),
  });
  const fork = useMutation({
    mutationFn: (v: { sourceKind: 'core' | 'def'; sourceId: string; kind: DefKind }) => api.post<DefDetail>('/api/agent-defs/fork', { ...v, scope: 'org' }),
    onSuccess: (d) => { void qc.invalidateQueries({ queryKey: ['agent-lists'] }); setCore(null); open(d.def.id); }, onError: (e) => setMsg(e instanceof Error ? e.message : 'Could not copy it'),
  });
  const share = useMutation({
    mutationFn: (v: { id: string; public: boolean }) => api.put(`/api/agent-library/core/${kind}/${v.id}/public`, { public: v.public }),
    onMutate: (v) => {            // the box answers the click at once; the server's answer follows
      qc.setQueryData<{ core: CoreCard[]; org: Card[]; projectOwned: number }>(['agent-lists', 'org', kind], (d) => (d ? { ...d, core: d.core.map((c) => (c.id === v.id ? { ...c, public: v.public } : c)) } : d));
    },
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ['agent-lists'] }); void qc.invalidateQueries({ queryKey: ['agent-starters'] }); }, onError: (e) => { setMsg(e instanceof Error ? e.message : 'Could not change that'); void qc.invalidateQueries({ queryKey: ['agent-lists'] }); },
  });
  const toggleOpen = useMutation({
    mutationFn: (v: { id: string; open: boolean }) => api.put(`/api/agent-defs/${v.id}/open`, { open: v.open }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['agent-lists'] }), onError: (e) => setMsg(e instanceof Error ? e.message : 'Could not change that'),
  });
  if (editing) return <AgentBuilder defId={editing} projectId={null} onBack={() => open(null)} />;
  const views: Array<['org' | 'core' | 'guardrails' | 'approvals', string]> = [['org', 'Organisation'], ['core', 'Built-in'], ['guardrails', 'Guardrails'], ['approvals', 'Approvals']];
  return (
    <div data-testid="v2-agent-library">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex gap-1" role="group" aria-label="Agents or skills">{(['agent', 'skill'] as const).map((k) => <button key={k} type="button" aria-pressed={kind === k} data-testid={`v2-lib-kind-${k}`} onClick={() => { setKind(k); setCore(null); }} className={`rounded-lg px-3 py-1 text-xs font-semibold ${kind === k ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-600'}`}>{k === 'agent' ? 'Agents' : 'Skills'}</button>)}</div>
        <div className="flex gap-1" role="tablist" aria-label="Library view">{views.map(([id, l]) => <button key={id} role="tab" aria-selected={view === id} data-testid={`v2-lib-view-${id}`} onClick={() => { setView(id); setCore(null); }} className={`rounded-lg px-3 py-1 text-xs font-semibold ${view === id ? 'bg-navy text-white' : 'bg-slate-100 text-slate-600'}`}>{l}</button>)}</div>
        <button type="button" className={primary} disabled={create.isPending} data-testid="v2-lib-new" onClick={() => create.mutate()}>+ New {kind}</button>
      </div>
      {msg && <div className="mb-2 rounded-lg bg-slate-100 px-3 py-1.5 text-xs text-slate-700" role="status">{msg}</div>}
      {view === 'approvals' && <ApprovalsView projectId={null} userId={userId} onOpen={(id) => open(id)} />}
      {view === 'guardrails' && <Guardrails />}
      {view === 'org' && (lib.isLoading ? <div className="animate-pulse text-sm text-slate-400">Loading…</div> : (
        <>
          <p className="mb-2 text-xs text-slate-500">{lib.data?.projectOwned ?? 0} agents and skills are private to projects and are not listed here. Open a published {kind} to projects and they can copy it.</p>
          {!(lib.data?.org.length) ? <div className="rounded-xl border border-slate-300 bg-white p-6 text-center text-sm text-slate-500" data-testid="v2-lib-empty">No organisation {kind}s yet. Create one, or copy a built-in one and extend it.</div> : (
            <ul className="grid gap-3 md:grid-cols-2" data-testid="v2-lib-org">
              {lib.data.org.map((c) => { const chip = statusChip(c.status, c.version); return (
                <li key={c.id} className="rounded-xl border border-slate-300 bg-white p-4" data-testid="v2-lib-card">
                  <div className="flex items-start justify-between gap-2"><div className="font-display text-base font-bold text-navy">{c.name}</div><div className="flex gap-1">{c.retired && <Pill>Retired</Pill>}<Pill tone={chip.tone}>{chip.label}</Pill></div></div>
                  <p className="mt-1 text-sm text-slate-600">{c.description || 'No description yet.'}</p>
                  <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                    <button type="button" className={btn} onClick={() => open(c.id)}>Open</button>
                    {c.publishedVersion && !c.retired && <button type="button" className={c.open ? primary : btn} data-testid="v2-lib-open-toggle" onClick={() => toggleOpen.mutate({ id: c.id, open: !c.open })}>{c.open ? 'Open to projects ✓' : 'Open to projects'}</button>}
                    <button type="button" className={btn} onClick={() => fork.mutate({ sourceKind: 'def', sourceId: c.id, kind })}>Copy</button>
                    <span>{c.usedInProjects ? `Used in ${c.usedInProjects} project${c.usedInProjects > 1 ? 's' : ''}` : 'Not used yet'}</span>
                  </div>
                </li>); })}
            </ul>)}
        </>
      ))}
      {view === 'core' && (core ? <CoreViewer id={core.id} kind={core.kind} onBack={() => setCore(null)} onFork={() => fork.mutate({ sourceKind: 'core', sourceId: core.id, kind: core.kind })} busy={fork.isPending} /> : (
        <>
          <p className="mb-2 text-xs text-slate-500">Built-in {kind}s ship with the platform and cannot be edited. Those marked as shared can be copied by every project, which can read the definition and extend it; copy one here to make an organisation version.</p>
          <ul className="grid gap-2 md:grid-cols-2" data-testid="v2-lib-core">
            {(lib.data?.core ?? []).map((c) => (
              <li key={c.id} className="rounded-xl border border-slate-300 bg-white p-3" data-testid="v2-lib-core-card">
                <button type="button" onClick={() => setCore({ id: c.id, kind })} className="w-full text-left">
                  <div className="flex items-start justify-between gap-2"><span className="font-semibold text-slate-800">{c.name}</span><Pill>🔒 Built-in</Pill></div>
                  <div className="mt-0.5 line-clamp-2 text-xs text-slate-500">{c.description}</div><div className="mt-1 flex gap-1 text-xs"><Pill>{c.roleLabel}</Pill>{c.stage != null && <Pill tone="brand">Stage {c.stage}</Pill>}</div>
                </button>
                <div className="mt-2 border-t border-slate-100 pt-2 text-xs">
                  {c.copyable ? <label className="flex items-center gap-2 text-slate-600"><input type="checkbox" data-testid="v2-core-public-toggle" checked={Boolean(c.public)} disabled={share.isPending} onChange={(e) => share.mutate({ id: c.id, public: e.target.checked })} /> Shared with projects, who can copy it</label>
                    : <span className="text-slate-400">Runs inside the platform, so it cannot be copied</span>}
                </div>
              </li>))}
          </ul>
        </>))}
    </div>
  );
}

function CoreViewer({ id, kind, onBack, onFork, busy }: { id: string; kind: DefKind; onBack: () => void; onFork: () => void; busy: boolean }) {
  const q = useQuery({ queryKey: ['agent-core', kind, id], queryFn: () => api.get<CoreCard & { prompt: string; notes: string }>(`/api/agent-library/core/${kind}/${id}`) });
  const d = q.data;
  return (
    <div className="rounded-xl border border-slate-300 bg-white p-4" data-testid="v2-core-viewer">
      <button type="button" onClick={onBack} className="text-xs font-semibold text-brand-700 hover:underline">← Back</button>
      {!d ? <div className="mt-2 animate-pulse text-sm text-slate-400">Loading…</div> : (
        <>
          <div className="mt-1 flex flex-wrap items-center justify-between gap-2"><h3 className="font-display text-lg font-bold text-navy">{d.name} <Pill>🔒 Read-only</Pill></h3><button type="button" className={primary} disabled={busy} data-testid="v2-core-copy" onClick={onFork}>Copy and extend</button></div>
          <p className="mt-1 text-sm text-slate-600">{d.description}</p>
          <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap rounded-lg border border-slate-200 bg-slate-50 p-3 font-mono text-xs">{d.prompt}</pre>
        </>)}
    </div>
  );
}

function Guardrails() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['agent-guardrails'], queryFn: () => api.get<{ guardrails: Guardrail[]; canEdit: boolean }>('/api/agent-guardrails') });
  const set = useMutation({ mutationFn: (v: { id: string; severity: string }) => api.put(`/api/agent-guardrails/${v.id}`, { severity: v.severity }), onSuccess: () => void qc.invalidateQueries({ queryKey: ['agent-guardrails'] }) });
  return (
    <div className="rounded-xl border border-slate-300 bg-white p-4" data-testid="v2-guardrails">
      <p className="mb-2 text-sm text-slate-600">Every custom agent and skill is checked against these before it can be submitted. A blocking guardrail stops the submission; a warning needs a person to accept it.</p>
      <table className="w-full text-sm"><thead><tr className="text-left text-xs text-slate-500"><th>Guardrail</th><th>Default</th><th>Now</th></tr></thead><tbody>
        {(q.data?.guardrails ?? []).map((g) => (
          <tr key={g.id} className="border-t border-slate-100"><td className="py-1.5 pr-2">{g.text}</td><td><Pill tone={g.default === 'block' ? 'red' : 'amber'}>{g.default === 'block' ? 'Blocks' : 'Warns'}</Pill></td>
            <td><select aria-label={`Severity for ${g.id}`} className="rounded border border-slate-300 px-1 py-0.5 text-xs" value={g.severity} disabled={!q.data?.canEdit} onChange={(e) => set.mutate({ id: g.id, severity: e.target.value })}><option value="block">Blocks</option><option value="warn">Warns</option></select></td></tr>))}
      </tbody></table>
    </div>
  );
}
