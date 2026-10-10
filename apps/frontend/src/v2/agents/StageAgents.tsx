import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../api/client';
import { PHASE_ROLES, WIRE_TONE, decodePalette, encodePalette, moveItem, type Rights, type StageAvailable, type StageItem } from '../../lib/agents';
import { Pill } from '../bits';

type Resp = { rights: Rights; items: StageItem[]; available: StageAvailable[]; stage: string };
type Row = { defId: string; pinnedVersion?: number; runs: StageItem['runs']; condition: string; roles: string[] };
const sel = 'rounded border border-slate-300 bg-white px-1.5 py-0.5 text-xs disabled:bg-slate-100';

/** Agents and skills attached to one stage of a project's workflow. The stage keeps the approved version it was given until someone moves it. */
export default function StageAgents({ projectId, stageKey }: { projectId: string; stageKey: string }) {
  const qc = useQueryClient();
  const key = ['stage-agents', projectId, stageKey];
  const q = useQuery({ queryKey: key, queryFn: () => api.get<Resp>(`/api/projects/${projectId}/stages/${encodeURIComponent(stageKey)}/agents`), retry: false });
  const [err, setErr] = useState('');
  const [over, setOver] = useState(false);
  const save = useMutation({
    mutationFn: (rows: Row[]) => api.put<Resp>(`/api/projects/${projectId}/stages/${encodeURIComponent(stageKey)}/agents`, { items: rows }),
    onSuccess: (d) => { setErr(''); qc.setQueryData(key, d); void qc.invalidateQueries({ queryKey: ['agent-lists'] }); void qc.invalidateQueries({ queryKey: ['agent-pipeline', projectId] }); }, onError: (e) => setErr(e instanceof Error ? e.message : 'Could not save'),
  });
  if (q.isError) return <div className="text-xs text-slate-500" data-testid="v2-stage-agents-unavailable">{(q.error as Error).message.includes('does not exist') ? 'Save the workflow first; agents can be attached once the stage exists.' : (q.error as Error).message}</div>;
  if (!q.data) return <div className="animate-pulse text-xs text-slate-400">Loading…</div>;
  const { items, available, rights } = q.data;
  const rows = (): Row[] => items.map((i) => ({ defId: i.defId, pinnedVersion: i.pinnedVersion, runs: i.runs, condition: i.condition, roles: i.roles }));
  const patch = (id: string, p: Partial<Row>) => save.mutate(rows().map((r) => (r.defId === id ? { ...r, ...p } : r)));
  const move = (from: number, to: number) => { const r = moveItem(rows(), from, to); if (r !== rows() && JSON.stringify(r) !== JSON.stringify(rows())) save.mutate(r); };
  const add = (id: string) => { const a = available.find((x) => x.defId === id); if (a) save.mutate([...rows(), { defId: id, runs: a.kind === 'skill' ? 'on_request' : 'always', condition: '', roles: [] }]); };
  const drop = (e: React.DragEvent) => { e.preventDefault(); setOver(false); const p = decodePalette(e.dataTransfer.getData('text/plain')); if (p?.kind === 'stageagent') add(p.value); };
  const editable = rights.edit;
  return (
    <div className="space-y-2 text-xs" data-testid="v2-stage-agents">
      <p className="text-slate-500">Custom agents run after the stage's own agents and add their results as artefacts. Skills are offered to the people working in the stage.</p>
      {err && <div className="rounded bg-bared-200 px-2 py-1 text-bared-700" role="alert">{err}</div>}
      <ul className="space-y-2" data-testid="v2-stage-agent-list">
        {items.map((i, n) => (
          <li key={i.defId} className="rounded-lg border border-slate-200 p-2" data-testid="v2-stage-agent" draggable={editable}
            onDragStart={(e) => { e.dataTransfer.setData('text/plain', `order|${n}`); e.dataTransfer.effectAllowed = 'move'; }}
            onDragOver={(e) => { if (editable) e.preventDefault(); }}
            onDrop={(e) => { const t = e.dataTransfer.getData('text/plain'); if (t.startsWith('order|')) { e.preventDefault(); e.stopPropagation(); move(Number(t.slice(6)), n); } }}>
            <div className="flex items-center justify-between gap-2"><span className="flex items-center gap-1"><span className="rounded-full bg-slate-100 px-1.5 text-[10px] font-semibold text-slate-500" title="The order they run in">{n + 1}</span><b>{i.name}</b></span><div className="flex items-center gap-1">
              <button type="button" aria-label={`Move ${i.name} earlier`} disabled={!editable || n === 0} data-testid="v2-stage-agent-up" className="px-1 text-slate-400 hover:text-slate-700 disabled:opacity-30" onClick={() => move(n, n - 1)}>↑</button>
              <button type="button" aria-label={`Move ${i.name} later`} disabled={!editable || n === items.length - 1} data-testid="v2-stage-agent-down" className="px-1 text-slate-400 hover:text-slate-700 disabled:opacity-30" onClick={() => move(n, n + 1)}>↓</button><Pill tone={i.kind === 'skill' ? 'brand' : 'green'}>{i.kind}</Pill><Pill>{i.source === 'org' ? 'Organisation' : 'This project'}</Pill>
              <button type="button" aria-label={`Remove ${i.name}`} disabled={!editable} className="text-slate-400 hover:text-bared-600" onClick={() => save.mutate(rows().filter((r) => r.defId !== i.defId))}>×</button></div></div>
            <div className="mt-1 text-slate-500">Approved version {i.pinnedVersion}{i.newerAvailable && <> · <button type="button" className="font-semibold text-brand-700 underline" disabled={!editable} data-testid="v2-stage-agent-upgrade" onClick={() => patch(i.defId, { pinnedVersion: i.publishedVersion ?? i.pinnedVersion })}>v{i.publishedVersion} is approved, use it</button></>}</div>
            {i.kind === 'agent' ? (
              <div className="mt-1.5 flex flex-wrap items-center gap-2">
                <label>Runs <select aria-label="When it runs" className={sel} disabled={!editable} value={i.runs} onChange={(e) => patch(i.defId, { runs: e.target.value as StageItem['runs'], condition: e.target.value === 'when' ? i.condition || 'len(brief) > 0' : '' })}>
                  <option value="always">Every time</option><option value="when">Only when…</option><option value="on_request">On request</option></select></label>
                {i.runs === 'when' && <input aria-label="Condition" className={`${sel} min-w-[10rem] flex-1`} disabled={!editable} defaultValue={i.condition} placeholder="for example len(brief) > 500" onBlur={(e) => e.target.value !== i.condition && patch(i.defId, { condition: e.target.value })} />}
              </div>
            ) : (
              <div className="mt-1.5 flex flex-wrap items-center gap-1">Offered to {PHASE_ROLES.map((r) => <button key={r} type="button" aria-pressed={i.roles.includes(r)} disabled={!editable} onClick={() => patch(i.defId, { roles: i.roles.includes(r) ? i.roles.filter((x) => x !== r) : [...i.roles, r] })} className={`rounded-full px-2 py-0.5 font-medium ${i.roles.includes(r) ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-600'}`}>{r}</button>)}{!i.roles.length && <span className="text-slate-400">everyone in the stage</span>}</div>
            )}
            {i.wiring && i.wiring.inputs.length > 0 && (
              <div className="mt-1.5 flex flex-wrap gap-1" data-testid="v2-wiring" data-blocked={i.wiring.blocked ? 'true' : 'false'}>
                {i.wiring.inputs.map((w) => <Pill key={w.name} tone={WIRE_TONE[w.status]} title={w.from}>{w.name} ← {w.from}</Pill>)}
              </div>)}
            {i.wiring?.blocked && <div className="mt-1 rounded bg-bared-200 px-2 py-1 text-bared-700" role="note" data-testid="v2-wiring-blocked">This agent would be skipped: an input it needs is not produced earlier. Move an agent that writes it above this one, or attach it to a later stage.</div>}
            {i.outputsDetail.length > 0 && <div className="mt-1 text-slate-500">Adds: {i.outputsDetail.map((o) => `${o.artefact_type} (${o.name})`).join(', ')}</div>}
          </li>))}
        {!items.length && <li className="text-slate-400">Nothing attached.</li>}
      </ul>
      {editable && (
        <div onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={drop} data-drop="stage" data-testid="v2-stage-agent-drop"
          className={`rounded-lg border border-dashed p-2 ${over ? 'border-brand-500 bg-brand-50' : 'border-slate-300'}`}>
          {available.length ? <>
            <div className="mb-1 text-slate-500">Drag one here, or pick it from the list.</div>
            <div className="mb-2 flex flex-wrap gap-1.5">{available.map((a) => (
              <button key={a.defId} type="button" draggable data-pal={encodePalette({ kind: 'stageagent', value: a.defId })} title={a.description} onClick={() => add(a.defId)} data-testid="v2-stage-agent-chip"
                onDragStart={(e) => { e.dataTransfer.setData('text/plain', encodePalette({ kind: 'stageagent', value: a.defId })); e.dataTransfer.effectAllowed = 'copy'; }}
                className="cursor-grab rounded-full border border-slate-300 bg-white px-2.5 py-0.5 font-semibold text-slate-700 hover:border-brand-400">{a.kind === 'skill' ? '✦ ' : '● '}{a.name} <span className="font-normal text-slate-400">v{a.version}</span></button>))}</div>
          </> : <div className="text-slate-500">No approved agents or skills are available yet. Build one in MindDesigner, then get it approved.</div>}
        </div>)}
      {!editable && <div className="text-slate-400">Ask the project manager for permission to change this.</div>}
    </div>
  );
}
