import { useState } from 'react';
import { agileApi, type AgileOverview } from '../../api/agile';
import type { FlowStage } from '../../api/flow';
import { capacityMeter, daysLeft, nextStep } from '../../lib/agileView';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Callout } from '../ui/Callout';
import { errorText, useAgileMutation, useBacklog } from './hooks';

const BAR: Record<string, string> = { success: 'bg-emerald-500', warning: 'bg-amber-500', error: 'bg-red-500', neutral: 'bg-slate-300', info: 'bg-blue-500' };

/** Where the project is: release, sprint, capacity — and the single next thing to do. */
export default function SprintHeader({ projectId, overview, stages, onOpenStage }: {
  projectId: string; overview: AgileOverview; stages: FlowStage[]; onOpenStage: (seq: number) => void;
}) {
  const it = overview.currentIteration ?? null;
  const rel = overview.currentRelease ?? null;
  const [goal, setGoal] = useState('');
  const [cap, setCap] = useState<number | ''>('');
  const [form, setForm] = useState(false);
  const items = useBacklog(projectId, it?.id);
  const committed = (items.data?.items ?? []).reduce((n, i) => n + (i.estimate ?? 0), 0);
  const meter = capacityMeter(committed, it?.capacity ?? 0);
  const start = useAgileMutation(projectId, () => agileApi.startSprint(projectId, { goal, capacity: cap === '' ? undefined : cap }));
  const harden = useAgileMutation(projectId, () => agileApi.harden(projectId, rel!.id));
  const cancel = useAgileMutation(projectId, () => agileApi.cancelSprint(projectId, it!.id));
  const step = nextStep(overview, stages);
  const left = daysLeft(it?.endsOn ?? null);
  const canRun = overview.permissions.canRun;
  const closedInRelease = (overview.iterations ?? []).some((i) => i.status === 'closed' && i.releaseId === rel?.id);
  const err = start.error ?? harden.error ?? cancel.error;

  return (
    <div className="space-y-3 border-b border-slate-200 bg-white px-5 py-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-base font-semibold text-slate-800">{it ? `${it.label}` : 'No sprint running'}</h2>
        {it && <Badge tone={it.status === 'active' ? 'info' : 'warning'}>{it.status === 'active' ? 'Active' : 'Planning'}</Badge>}
        {rel && <Badge tone="brand" icon="flag">{rel.code} · {rel.status}</Badge>}
        <Badge icon="target">{overview.methodology === 'kanban' ? 'Kanban' : 'Scrum'}</Badge>
        {left !== null && it && <Badge tone={left < 0 ? 'error' : left <= 2 ? 'warning' : 'neutral'} icon="clock">{left < 0 ? `${-left} day(s) over` : `${left} day(s) left`}</Badge>}
        {overview.averageVelocity ? <Badge title="Average of the last three sprints">Velocity {overview.averageVelocity} pts</Badge> : null}
        <div className="ml-auto flex items-center gap-2">
          {canRun && !it && rel?.status === 'open' && (
            <Button variant="primary" icon="play" onClick={() => setForm((v) => !v)}>Start sprint</Button>)}
          {canRun && !it && rel?.status === 'open' && closedInRelease && (
            <Button icon="shield" loading={harden.isPending} onClick={() => harden.mutate(undefined)}
              title="Close the release: a final hardening stage, then the release is committed">Harden &amp; release</Button>)}
          {canRun && it && it.status === 'planned' && (
            <Button size="sm" variant="ghost" icon="x" loading={cancel.isPending}
              onClick={() => { if (window.confirm(`Cancel ${it.label}? Only possible before any stage has started.`)) cancel.mutate(undefined); }}>Cancel sprint</Button>)}
        </div>
      </div>

      {it?.goal && <p className="text-sm text-slate-600"><span className="font-semibold text-slate-700">Goal:</span> {it.goal}</p>}
      {it && (
        <div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100" role="progressbar" aria-valuenow={meter.percent} aria-valuemin={0} aria-valuemax={100} aria-label="Sprint capacity">
            <div className={`h-full ${BAR[meter.tone]}`} style={{ width: `${Math.min(100, meter.percent)}%` }} />
          </div>
          <div className={`mt-1 text-xs ${meter.tone === 'error' ? 'font-semibold text-red-700' : meter.tone === 'warning' ? 'text-amber-800' : 'text-slate-500'}`}>{meter.label}</div>
        </div>
      )}

      {form && !it && (
        <div className="grid gap-3 rounded-lg border border-slate-200 bg-slate-50 p-3 sm:grid-cols-[1fr_140px_auto]">
          <label className="text-xs font-semibold text-slate-600">Sprint goal
            <input value={goal} maxLength={240} onChange={(e) => setGoal(e.target.value)} placeholder="What should be true at the end of this sprint?"
              className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm font-normal" />
          </label>
          <label className="text-xs font-semibold text-slate-600">Capacity (pts)
            <input type="number" min={0} value={cap} placeholder={String(overview.settings?.defaultCapacity ?? '')} onChange={(e) => setCap(e.target.value === '' ? '' : Number(e.target.value))}
              className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm font-normal" />
          </label>
          <div className="flex items-end"><Button variant="primary" icon="play" loading={start.isPending}
            onClick={() => start.mutate(undefined, { onSuccess: () => { setForm(false); setGoal(''); } })}>Start</Button></div>
        </div>
      )}

      {err && <Callout tone="error" compact title="That did not work">{errorText(err)}</Callout>}
      <Callout tone={step.tone} compact title={step.title}
        action={step.action === 'open-stage' && step.stageSeq ? <Button size="sm" variant="primary" icon="arrow-right" onClick={() => onOpenStage(step.stageSeq!)}>Open</Button>
          : step.action === 'start-sprint' && canRun && !it ? <Button size="sm" variant="primary" icon="play" onClick={() => setForm(true)}>Start sprint</Button> : undefined}>
        {step.body}
      </Callout>
    </div>
  );
}
