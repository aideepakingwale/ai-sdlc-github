import { agileApi, type AgileOverview, type BacklogItem } from '../../api/agile';
import { capacityMeter } from '../../lib/agileView';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Callout } from '../ui/Callout';
import { Icon } from '../ui/Icon';
import { errorText, useAgileMutation, useBacklog } from './hooks';

const COLUMNS: Array<{ id: 'in_sprint' | 'in_progress' | 'done'; title: string; hint: string }> = [
  { id: 'in_sprint', title: 'To do', hint: 'Committed, not started' },
  { id: 'in_progress', title: 'In progress', hint: 'Being worked on' },
  { id: 'done', title: 'Done', hint: 'Accepted by the Product Owner' },
];

/** The sprint at a glance: committed items by state, with the points to prove it. */
export default function SprintBoard({ projectId, overview }: { projectId: string; overview: AgileOverview }) {
  const it = overview.currentIteration ?? null;
  const list = useBacklog(projectId, it?.id);
  const act = useAgileMutation(projectId, (fn: () => Promise<unknown>) => fn());
  if (!it) {
    return (
      <div className="m-5 rounded-xl border border-dashed border-slate-300 py-12 text-center text-sm text-slate-500">
        <Icon name="flag" size={28} className="mx-auto mb-2 text-slate-300" />
        No sprint is running. Start one from the header, then plan it to fill this board.
      </div>
    );
  }
  const items = list.data?.items ?? [];
  const pts = (s: BacklogItem['status']) => items.filter((i) => i.status === s).reduce((n, i) => n + (i.estimate ?? 0), 0);
  const total = items.reduce((n, i) => n + (i.estimate ?? 0), 0);
  const donePct = total ? Math.round((pts('done') / total) * 100) : 0;
  const meter = capacityMeter(total, it.capacity);
  const canRun = overview.permissions.canRun;

  return (
    <div className="space-y-4 p-5">
      {act.isError && <Callout tone="error" compact title="That did not work">{errorText(act.error)}</Callout>}
      <div className="grid gap-3 sm:grid-cols-3">
        <Stat label="Committed" value={`${total} pts`} tone={meter.tone === 'error' ? 'text-red-700' : 'text-slate-800'} sub={meter.label} />
        <Stat label="Done" value={`${pts('done')} pts`} sub={`${donePct}% of the commitment`} tone="text-emerald-700" />
        <Stat label="Remaining" value={`${pts('in_sprint') + pts('in_progress')} pts`} sub={`${items.filter((i) => i.status !== 'done').length} item(s) open`} tone="text-slate-800" />
      </div>
      <div className="h-2.5 w-full overflow-hidden rounded-full bg-slate-100" aria-label="Sprint progress" role="progressbar" aria-valuenow={donePct} aria-valuemin={0} aria-valuemax={100}>
        <div className="flex h-full">
          <div className="bg-emerald-500" style={{ width: `${total ? (pts('done') / total) * 100 : 0}%` }} />
          <div className="bg-amber-400" style={{ width: `${total ? (pts('in_progress') / total) * 100 : 0}%` }} />
        </div>
      </div>
      {items.length === 0 && <Callout tone="advice" compact title="Nothing is committed yet">Approve the Sprint Planning stage to commit the plan, or add ready items from the backlog.</Callout>}
      <div className="grid gap-4 lg:grid-cols-3">
        {COLUMNS.map((c) => {
          const col = items.filter((i) => i.status === c.id);
          return (
            <section key={c.id} className="rounded-xl border border-slate-200 bg-slate-50/60" aria-label={c.title}>
              <header className="flex items-center justify-between border-b border-slate-200 px-3 py-2">
                <div><h3 className="text-sm font-semibold text-slate-700">{c.title}</h3><p className="text-[11px] text-slate-400">{c.hint}</p></div>
                <Badge>{col.length} · {pts(c.id)} pts</Badge>
              </header>
              <ul className="space-y-2 p-2">
                {col.map((i) => (
                  <li key={i.id} className="rounded-lg border border-slate-200 bg-white p-2.5 shadow-sm">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0"><span className="font-mono text-[11px] text-slate-400">{i.key}</span>
                        <div className="text-sm font-medium text-slate-800">{i.title}</div></div>
                      <Badge>{i.estimate ?? '?'} pts</Badge>
                    </div>
                    {i.acceptanceCriteria.length > 0 && <div className="mt-1 text-[11px] text-slate-500">{i.acceptanceCriteria.length} acceptance criteria</div>}
                    <div className="mt-2 flex gap-1.5">
                      {c.id === 'in_sprint' && <Button size="sm" variant="secondary" icon="play" onClick={() => act.mutate(() => agileApi.setStatus(projectId, i.key, 'in_progress'))}>Start</Button>}
                      {c.id === 'in_progress' && canRun && <Button size="sm" variant="primary" icon="check" onClick={() => act.mutate(() => agileApi.setStatus(projectId, i.key, 'done'))}>Accept as done</Button>}
                      {c.id === 'in_progress' && <Button size="sm" variant="ghost" onClick={() => act.mutate(() => agileApi.setStatus(projectId, i.key, 'in_sprint'))}>Back</Button>}
                      {c.id === 'done' && canRun && <Button size="sm" variant="ghost" onClick={() => act.mutate(() => agileApi.setStatus(projectId, i.key, 'in_progress'))}>Reopen</Button>}
                    </div>
                  </li>
                ))}
                {col.length === 0 && <li className="px-2 py-6 text-center text-xs text-slate-400">Empty</li>}
              </ul>
            </section>
          );
        })}
      </div>
      {(overview.velocity?.length ?? 0) > 0 && <Velocity data={overview.velocity!} />}
    </div>
  );
}

function Stat({ label, value, sub, tone }: { label: string; value: string; sub: string; tone: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">{label}</div>
      <div className={`text-xl font-semibold ${tone}`}>{value}</div>
      <div className="text-xs text-slate-500">{sub}</div>
    </div>
  );
}

function Velocity({ data }: { data: Array<{ sprint: string; points: number; completed: number }> }) {
  const max = Math.max(1, ...data.map((d) => d.points));
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4" aria-label="Velocity">
      <h3 className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-slate-700"><Icon name="chart" size={14} />Velocity — points delivered per sprint</h3>
      <div className="flex h-28 items-end gap-2">
        {data.slice(-12).map((d) => (
          <div key={d.sprint} className="flex h-full flex-1 flex-col items-center justify-end gap-1" title={`${d.sprint}: ${d.points} pts, ${d.completed} item(s)`}>
            <span className="text-[10px] font-semibold text-slate-600">{d.points}</span>
            <div className="w-full max-w-[40px] rounded-t bg-brand-500" style={{ height: `${(d.points / max) * 70}%`, minHeight: 2 }} />
            <span className="text-[10px] text-slate-400">{d.sprint}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
