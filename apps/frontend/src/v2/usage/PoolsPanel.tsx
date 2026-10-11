import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../api/client';
import { tokensLabel } from '../../lib/agents';
import { parseTokens, signedTokens, sortPools, STATE_LABEL, STATE_TONE, usedShare, type PoolDetail, type PoolsOverview, type PoolView } from '../../lib/pools';
import { Pill } from '../bits';

const LIMIT = 12;
type Target = { scope: 'org' | 'project'; id: string; name: string; pool: PoolView };
const BAR: Record<string, string> = { green: 'bg-brand-500', slate: 'bg-slate-300', amber: 'bg-amber-500', red: 'bg-red-500' };

/** The allocation console (administrators): the organisation's tokens, and what each project has been given. */
export default function PoolsPanel() {
  const q = useQuery({ queryKey: ['pools'], queryFn: () => api.get<PoolsOverview>('/api/token-pools') });
  const [target, setTarget] = useState<Target | null>(null);
  const [find, setFind] = useState('');
  const [all, setAll] = useState(false);
  const o = q.data;
  if (!o) return <div className="animate-pulse text-sm text-slate-400">Loading…</div>;
  const org = o.organisation;
  const rows = sortPools(o.projects);
  const needle = find.trim().toLowerCase();
  const shown = needle ? rows.filter((p) => p.name.toLowerCase().includes(needle)) : all ? rows : rows.slice(0, LIMIT);
  return (
    <section className="rounded-xl border border-slate-300 bg-white p-4" data-testid="v2-pools">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div><h2 className="font-display text-base font-bold text-navy">Token allocations</h2><p className="text-xs text-slate-500">What the organisation may spend, and what each project has been given. Balances can go below zero: the activity in flight finishes.</p></div>
        <button type="button" className="rounded-lg border border-slate-300 bg-white px-3 py-1 text-sm font-medium text-navy hover:bg-slate-100" onClick={() => setTarget({ scope: 'org', id: 'org', name: 'The organisation', pool: org })} data-testid="v2-pool-org-allocate">
          {org.metered ? 'Add to the organisation’s tokens' : 'Set the organisation’s tokens'}
        </button>
      </div>
      <div className="mb-3 grid gap-3 sm:grid-cols-3" data-testid="v2-pool-org">
        <Stat label="Organisation" value={org.metered ? signedTokens(org.balance ?? 0) : 'Not limited'} sub={org.metered ? `${tokensLabel(org.consumed)} used of ${tokensLabel(org.allocated)}` : 'No allocation set'} pill={org.metered ? org.state : undefined} />
        <Stat label="Given to projects" value={tokensLabel(o.allocatedToProjects)} sub={o.unallocated === null ? 'across all projects' : `${signedTokens(o.unallocated)} not yet given out`} />
        <Stat label="Monthly top-up" value={org.monthly ? tokensLabel(org.monthly) : 'None'} sub={org.monthly ? 'added on the 1st (UTC)' : 'one-off allocations only'} />
      </div>
      {o.overAllocated && <p className="mb-3 rounded-lg border border-amber-300 bg-amber-50 p-2 text-sm text-amber-900" role="status" data-testid="v2-pool-over">Projects have been given more tokens than the organisation has.</p>}
      <div className="mb-2 flex items-center gap-2"><input value={find} onChange={(e) => setFind(e.target.value)} placeholder="Find a project" aria-label="Find a project" className="w-56 rounded-lg border border-slate-300 px-3 py-1 text-sm" data-testid="v2-pool-find" /><span className="text-xs text-slate-500">Projects that need attention come first.</span></div>
      <table className="w-full text-sm"><thead><tr className="text-left text-xs uppercase tracking-wide text-slate-500"><th className="py-1">Project</th><th>Status</th><th className="w-40">Used</th><th className="text-right">Left</th><th className="text-right">Last 30 days</th><th /></tr></thead>
        <tbody>{shown.map((p) => {
          const tone = STATE_TONE[p.state];
          return (
            <tr key={p.projectId} className="border-t border-slate-200" data-testid="v2-pool-row">
              <td className="py-1.5 font-medium text-navy">{p.name}</td>
              <td><Pill tone={tone}>{STATE_LABEL[p.state]}</Pill></td>
              <td>{p.metered ? <span className="block h-1.5 rounded-full bg-slate-200"><span className={`block h-1.5 rounded-full ${BAR[tone]}`} style={{ width: `${Math.min(100, Math.round(usedShare(p) * 100))}%` }} /></span> : <span className="text-xs text-slate-400">—</span>}</td>
              <td className="text-right tabular-nums">{p.metered ? signedTokens(p.balance ?? 0) : '—'}</td>
              <td className="text-right tabular-nums text-slate-600">{tokensLabel(p.used30d)}</td>
              <td className="text-right"><button type="button" className="text-brand-600 hover:underline" onClick={() => setTarget({ scope: 'project', id: p.projectId, name: p.name, pool: p })} data-testid="v2-pool-allocate">{p.metered ? 'Add or change' : 'Set tokens'}</button></td>
            </tr>
          );
        })}</tbody></table>
      {!find && rows.length > LIMIT && <button type="button" className="mt-2 text-sm text-brand-600 hover:underline" onClick={() => setAll(!all)} data-testid="v2-pool-more">{all ? 'Show fewer' : `Show all ${rows.length} projects`}</button>}
      {target && <AllocateDialog target={target} onClose={() => setTarget(null)} />}
    </section>
  );
}

function Stat({ label, value, sub, pill }: { label: string; value: string; sub: string; pill?: PoolView['state'] }) {
  return (
    <div className="rounded-lg border border-slate-200 p-3">
      <div className="flex items-center justify-between text-xs font-semibold uppercase tracking-wide text-slate-500"><span>{label}</span>{pill && <Pill tone={STATE_TONE[pill]}>{STATE_LABEL[pill]}</Pill>}</div>
      <div className="font-display text-xl font-bold text-navy">{value}</div>
      <div className="text-xs text-slate-500">{sub}</div>
    </div>
  );
}

const PRESETS = ['100k', '1M', '10M', '100M'];

function AllocateDialog({ target, onClose }: { target: Target; onClose: () => void }) {
  const qc = useQueryClient();
  const [amount, setAmount] = useState('');
  const [kind, setKind] = useState<'one_off' | 'monthly'>('one_off');
  const [note, setNote] = useState('');
  const [countFrom, setCountFrom] = useState<'now' | 'month_start'>('now');
  const [err, setErr] = useState('');
  const detail = useQuery({ queryKey: ['pool-detail', target.scope, target.id], queryFn: () => api.get<PoolDetail>(`/api/token-pools/${target.scope}/${target.id}`) });
  const tokens = parseTokens(amount);
  const refresh = () => { void qc.invalidateQueries({ queryKey: ['pools'] }); void qc.invalidateQueries({ queryKey: ['project-pool'] }); void qc.invalidateQueries({ queryKey: ['pool-detail'] }); };
  const save = useMutation({
    mutationFn: () => api.post<{ warning?: string }>(`/api/token-pools/${target.scope}/${target.id}/allocations`, { tokens, kind, note, countFrom }),
    onSuccess: (r) => { refresh(); if (r.warning) setErr(r.warning); else onClose(); },
    onError: (e) => setErr(e instanceof Error ? e.message : 'Could not save'),
  });
  const stop = useMutation({ mutationFn: (id: string) => api.del(`/api/token-schedules/${id}`), onSuccess: refresh });
  const taking = tokens !== null && tokens < 0;
  const problem = tokens === null ? 'Type an amount such as 500k or 2M' : tokens === 0 ? 'The amount cannot be zero' : taking && kind === 'monthly' ? 'A monthly top-up must be positive' : taking && !note.trim() ? 'Say why tokens are being taken back' : '';
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true" aria-label={`Allocate tokens to ${target.name}`} data-testid="v2-pool-dialog">
      <div className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-xl bg-white p-5 shadow-xl">
        <h3 className="font-display text-lg font-bold text-navy">{target.name}</h3>
        <p className="mb-3 text-sm text-slate-600">{target.pool.metered ? `${signedTokens(target.pool.balance ?? 0)} tokens left of ${tokensLabel(target.pool.allocated)} allocated.` : 'Tokens are not limited yet. Allocating starts counting use against the allocation.'}</p>
        <label className="block text-sm font-medium text-navy" htmlFor="pool-amount">Tokens to add</label>
        <input id="pool-amount" value={amount} onChange={(e) => { setAmount(e.target.value); setErr(''); }} placeholder="e.g. 2M, 500k or 1,200,000" className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm" data-testid="v2-pool-amount" autoFocus />
        <div className="mt-1 flex flex-wrap gap-1.5">{PRESETS.map((p) => <button key={p} type="button" className="rounded-full border border-slate-300 px-2.5 py-0.5 text-xs text-slate-600 hover:bg-slate-100" onClick={() => { setAmount(p); setErr(''); }}>{p}</button>)}</div>
        <p className="mt-1 text-xs text-slate-500">A negative number takes tokens back (and needs a reason).</p>
        <fieldset className="mt-3 text-sm"><legend className="font-medium text-navy">How often</legend>
          <label className="mr-4"><input type="radio" name="kind" checked={kind === 'one_off'} onChange={() => setKind('one_off')} data-testid="v2-pool-oneoff" /> Once</label>
          <label><input type="radio" name="kind" checked={kind === 'monthly'} onChange={() => setKind('monthly')} data-testid="v2-pool-monthly" /> Now, and again on the 1st of every month</label></fieldset>
        {!target.pool.metered && (
          <fieldset className="mt-3 text-sm"><legend className="font-medium text-navy">Start counting use</legend>
            <label className="mr-4"><input type="radio" name="from" checked={countFrom === 'now'} onChange={() => setCountFrom('now')} /> From now</label>
            <label><input type="radio" name="from" checked={countFrom === 'month_start'} onChange={() => setCountFrom('month_start')} /> From the start of this month</label></fieldset>
        )}
        <label className="mt-3 block text-sm font-medium text-navy" htmlFor="pool-note">Note</label>
        <input id="pool-note" value={note} onChange={(e) => setNote(e.target.value)} maxLength={300} placeholder="Why (optional when adding)" className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm" data-testid="v2-pool-note" />
        {(problem && amount) ? <p className="mt-2 text-xs text-slate-500">{problem}</p> : null}
        {err && <p className="mt-2 text-sm text-amber-800" role="alert" data-testid="v2-pool-warning">{err}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm" onClick={onClose}>{err && !save.isPending && !problem ? 'Close' : 'Cancel'}</button>
          <button type="button" disabled={Boolean(problem) || save.isPending} onClick={() => save.mutate()} className="rounded-lg bg-brand-600 px-4 py-1.5 text-sm font-medium text-white disabled:opacity-40" data-testid="v2-pool-save">{save.isPending ? 'Saving…' : taking ? 'Take back' : 'Allocate'}</button>
        </div>
        {detail.data && (
          <div className="mt-4 border-t border-slate-200 pt-3 text-sm" data-testid="v2-pool-history">
            {detail.data.schedules.length > 0 && (
              <div className="mb-2"><div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Monthly top-ups</div>
                {detail.data.schedules.map((s) => <div key={s.id} className="flex items-center justify-between"><span>{tokensLabel(s.tokens)} on the 1st · next {s.nextRunAt.slice(0, 10)}{s.note ? ` · ${s.note}` : ''}</span><button type="button" className="text-red-700 hover:underline" onClick={() => stop.mutate(s.id)}>Stop</button></div>)}</div>
            )}
            <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Recent changes</div>
            {detail.data.grants.length === 0 ? <p className="text-slate-500">None yet.</p> : (
              <ul className="mt-1 space-y-0.5">{detail.data.grants.map((g) => <li key={g.id} className="flex justify-between gap-2"><span className="truncate">{g.tokens > 0 ? '+' : '−'}{tokensLabel(Math.abs(g.tokens))} {g.kind === 'monthly' ? 'monthly' : ''} · {g.by || 'system'}{g.note ? ` · ${g.note}` : ''}</span><span className="shrink-0 text-xs text-slate-500">{g.at.slice(0, 10)}</span></li>)}</ul>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
