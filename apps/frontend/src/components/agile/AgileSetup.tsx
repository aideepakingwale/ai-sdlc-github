import { useState } from 'react';
import type { Methodology } from '../../api/agile';
import { agileApi } from '../../api/agile';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Callout } from '../ui/Callout';
import { Icon, type IconName } from '../ui/Icon';
import { errorText, useAgileMutation } from './hooks';

const OPTIONS: Array<{ id: Exclude<Methodology, 'waterfall'>; title: string; icon: IconName; blurb: string; good: string[] }> = [
  {
    id: 'scrum', title: 'Scrum', icon: 'flag',
    blurb: 'Plan work in fixed-length sprints with a goal and a capacity.',
    good: ['Refine → Plan → Build → Review → Retro every sprint', 'Capacity-checked sprint commitments', 'Velocity and burndown'],
  },
  {
    id: 'kanban', title: 'Kanban', icon: 'layers',
    blurb: 'Continuous flow with a limit on work in progress — no sprint commitment.',
    good: ['Pull work as capacity frees up', 'WIP limit enforced', 'Regular review cycles'],
  },
];

/** First-run choice for a project that has not started: how will it be delivered? */
export default function AgileSetup({ projectId, onDone }: { projectId: string; onDone: () => void }) {
  const [method, setMethod] = useState<'scrum' | 'kanban'>('scrum');
  const [days, setDays] = useState(14);
  const [capacity, setCapacity] = useState(30);
  const [wip, setWip] = useState(3);
  const [strategy, setStrategy] = useState<'index-branch' | 'default-branch'>('index-branch');
  const [score, setScore] = useState(80);
  const enable = useAgileMutation(projectId, () =>
    agileApi.enable(projectId, {
      methodology: method, sprintDays: days, defaultCapacity: capacity, wipLimit: method === 'kanban' ? wip : null,
      indexStrategy: strategy, autoMinScore: score,
    }));

  return (
    <div className="mx-auto max-w-3xl space-y-5 p-6">
      <div>
        <h2 className="text-lg font-semibold text-slate-800">How will this project be delivered?</h2>
        <p className="mt-1 text-sm text-slate-500">
          This is chosen once, before any work starts. The classic waterfall pipeline is unchanged for projects that already use it.
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {OPTIONS.map((o) => (
          <button
            key={o.id} type="button" onClick={() => setMethod(o.id)} aria-pressed={method === o.id}
            className={`rounded-xl border p-4 text-left transition ${method === o.id ? 'border-brand-400 bg-brand-50/50 ring-1 ring-brand-300' : 'border-slate-200 bg-white hover:border-slate-300'}`}
          >
            <div className="flex items-center gap-2 text-sm font-semibold text-slate-800"><Icon name={o.icon} size={16} />{o.title}
              {method === o.id && <Badge tone="brand" icon="check">Selected</Badge>}</div>
            <p className="mt-1 text-xs text-slate-500">{o.blurb}</p>
            <ul className="mt-2 space-y-0.5 text-xs text-slate-600">{o.good.map((g) => <li key={g} className="flex gap-1.5"><Icon name="check" size={12} className="mt-0.5 text-emerald-600" />{g}</li>)}</ul>
          </button>
        ))}
      </div>

      <div className="grid gap-4 rounded-xl border border-slate-200 bg-white p-4 sm:grid-cols-2">
        {method === 'scrum' ? (
          <label className="text-xs font-semibold text-slate-600">Sprint length (days)
            <input type="number" min={1} max={42} value={days} onChange={(e) => setDays(Number(e.target.value))}
              className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm font-normal" />
          </label>
        ) : (
          <label className="text-xs font-semibold text-slate-600">Work-in-progress limit
            <input type="number" min={1} max={100} value={wip} onChange={(e) => setWip(Number(e.target.value))}
              className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm font-normal" />
          </label>
        )}
        <label className="text-xs font-semibold text-slate-600">{method === 'scrum' ? 'Default capacity per sprint (points)' : 'Points per cycle'}
          <input type="number" min={0} value={capacity} onChange={(e) => setCapacity(Number(e.target.value))}
            className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm font-normal" />
        </label>
        <label className="text-xs font-semibold text-slate-600">Auto-approve builds scoring at least
          <input type="number" min={0} max={100} value={score} onChange={(e) => setScore(Number(e.target.value))}
            className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm font-normal" />
          <span className="mt-1 block text-[11px] font-normal text-slate-500">The independent validator must reach this score, with nothing open, for the Build gate to pass without a person. Anything doubtful still goes to review.</span>
        </label>
        <label className="text-xs font-semibold text-slate-600">Where project memory is committed
          <select value={strategy} onChange={(e) => setStrategy(e.target.value as typeof strategy)}
            className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm font-normal">
            <option value="index-branch">A separate branch + a pull request at release close (recommended)</option>
            <option value="default-branch">Directly on the default branch (no branch protection)</option>
          </select>
        </label>
      </div>

      {enable.isError && <Callout tone="error" title="Could not enable it">{errorText(enable.error)}</Callout>}
      <div className="flex items-center gap-3">
        <Button variant="primary" icon="check" loading={enable.isPending}
          onClick={() => enable.mutate(undefined, { onSuccess: onDone })}>Use {method === 'scrum' ? 'Scrum' : 'Kanban'}</Button>
        <span className="text-xs text-slate-500">You can still change capacity, WIP limit and the auto-approve bar later.</span>
      </div>
    </div>
  );
}
