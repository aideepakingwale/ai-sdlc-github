import { useState } from 'react';
import { agileApi, type AgileOverview, type BacklogItem } from '../../api/agile';
import { applyFilter, countByStatus, FILTERS, filterCounts, STATUS_LABEL, STATUS_TONE, type BacklogFilter } from '../../lib/agileView';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Callout } from '../ui/Callout';
import { Icon, type IconName } from '../ui/Icon';
import JiraBar from './JiraBar';
import { errorText, useAgileMutation, useBacklog, type BacklogScope } from './hooks';

const TYPE_ICON: Record<string, IconName> = { epic: 'layers', story: 'file', bug: 'warning', task: 'tasks' };
const SCALE = [0.5, 1, 2, 3, 5, 8, 13, 21];

export default function BacklogView({ projectId, overview }: { projectId: string; overview: AgileOverview }) {
  const [filter, setFilter] = useState<BacklogFilter>('needs-work');
  const [query, setQuery] = useState('');
  const [title, setTitle] = useState('');
  const [open, setOpen] = useState<string | null>(null);
  const rel = overview.currentRelease ?? null;
  const [view, setView] = useState<'release' | 'pool' | 'all'>(rel ? 'release' : 'all');
  const scope: BacklogScope = view === 'all' || !rel ? 'all' : view === 'pool' ? 'pool' : rel.usePool === false ? 'release' : 'eligible';
  const list = useBacklog(projectId, undefined, { release: rel?.id, scope });
  const canEdit = overview.permissions.canRun;
  const sprintOpen = Boolean(overview.currentIteration);
  const sprintId = overview.currentIteration?.id;
  const counts = filterCounts(scope === 'all' ? overview.backlog : countByStatus(list.data?.items ?? []));
  const all = list.data?.items ?? [];
  const items = applyFilter(all, filter).filter((i) => !query || `${i.key} ${i.title}`.toLowerCase().includes(query.toLowerCase()));
  const create = useAgileMutation(projectId, (t: string) => agileApi.createItem(projectId, { title: t, ...(rel && view === 'release' ? { releaseId: rel.id } : {}) }));
  const act = useAgileMutation(projectId, (fn: () => Promise<unknown>) => fn());

  return (
    <div className="space-y-3 p-5">
      <JiraBar projectId={projectId} canSync={canEdit} />
      {rel && (
        <div className="flex flex-wrap items-center gap-1.5" role="tablist" aria-label="Backlog scope">
          {([['release', `${rel.code} backlog`], ['pool', 'Shared pool'], ['all', 'Everything']] as const).map(([id, label]) => (
            <button key={id} role="tab" aria-selected={view === id} onClick={() => setView(id)}
              className={`rounded-lg border px-3 py-1 text-xs font-semibold ${view === id ? 'border-brand-500 bg-brand-50 text-brand-700' : 'border-slate-200 text-slate-600 hover:border-slate-300'}`}>{label}</button>))}
          <span className="text-xs text-slate-400">{view === 'pool' ? 'Issues that belong to no release yet. Claim them into a release to plan them.'
            : view === 'release' ? (rel.usePool === false ? 'Only this release\'s items.' : 'This release\'s items, plus the shared pool.') : 'Every item of the project.'}</span>
        </div>)}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="Backlog filter">
          {FILTERS.map((f) => (
            <button key={f.id} role="tab" aria-selected={filter === f.id} onClick={() => setFilter(f.id)}
              className={`rounded-full px-3 py-1 text-xs font-semibold transition ${filter === f.id ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>
              {f.label} <span className={filter === f.id ? 'text-white/80' : 'text-slate-400'}>{counts[f.id]}</span>
            </button>
          ))}
        </div>
        <div className="relative ml-auto">
          <Icon name="search" size={14} className="pointer-events-none absolute left-2.5 top-2 text-slate-400" />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search the backlog" aria-label="Search the backlog"
            className="w-56 rounded-lg border border-slate-300 py-1.5 pl-8 pr-2 text-sm" />
        </div>
      </div>

      {canEdit && (
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (title.trim()) create.mutate(title.trim(), { onSuccess: () => setTitle('') }); }}>
          <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} placeholder="Add an item and press Enter — you can add acceptance criteria and an estimate afterwards"
            aria-label="New backlog item" className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          <Button type="submit" variant="primary" icon="plus" loading={create.isPending} disabled={!title.trim()}>Add</Button>
        </form>
      )}
      {(create.isError || act.isError) && <Callout tone="error" compact title="That did not work">{errorText(create.error ?? act.error)}</Callout>}

      {list.isLoading ? <div className="py-10 text-center text-sm text-slate-400">Loading the backlog…</div>
        : items.length === 0 ? (
          <div className="rounded-xl border border-dashed border-slate-300 py-10 text-center text-sm text-slate-500">
            <Icon name="inbox" size={28} className="mx-auto mb-2 text-slate-300" />
            {all.length === 0 ? 'The backlog is empty. Add items above, import them from Jira, or run the Refine stage and let the AI propose them.' : 'Nothing here — try another filter.'}
          </div>
        ) : (
          <ul className="divide-y divide-slate-100 overflow-hidden rounded-xl border border-slate-200 bg-white">
            {items.map((i, n) => (
              <Row key={i.id} item={i} canEdit={canEdit} sprintOpen={sprintOpen} sprintId={sprintId} claimInto={rel && rel.status !== 'closed' ? rel.id : null} isOpen={open === i.id} first={n === 0} last={n === items.length - 1}
                neighbours={{ prev: items[n - 1], next: items[n + 1] }}
                onToggle={() => setOpen(open === i.id ? null : i.id)}
                run={(fn) => act.mutate(fn)} projectId={projectId} />
            ))}
          </ul>
        )}
    </div>
  );
}

function Row({ item: i, canEdit, sprintOpen, sprintId, claimInto, isOpen, first, last, neighbours, onToggle, run, projectId }: {
  item: BacklogItem; canEdit: boolean; sprintOpen: boolean; sprintId?: string; claimInto: string | null; isOpen: boolean; first: boolean; last: boolean;
  neighbours: { prev?: BacklogItem; next?: BacklogItem }; onToggle: () => void; run: (fn: () => Promise<unknown>) => void; projectId: string;
}) {
  const movable = canEdit && ['new', 'refined', 'ready'].includes(i.status);
  return (
    <li className="px-3 py-2.5">
      <div className="flex items-center gap-2">
        {movable ? (
          <div className="flex flex-col">
            <button aria-label={`Move ${i.key} up`} disabled={first} onClick={() => run(() => agileApi.move(projectId, i.key, { before: neighbours.prev!.key }))}
              className="text-slate-400 hover:text-brand-600 disabled:opacity-30"><Icon name="chevron-down" size={13} className="rotate-180" /></button>
            <button aria-label={`Move ${i.key} down`} disabled={last} onClick={() => run(() => agileApi.move(projectId, i.key, { after: neighbours.next!.key }))}
              className="text-slate-400 hover:text-brand-600 disabled:opacity-30"><Icon name="chevron-down" size={13} /></button>
          </div>
        ) : <span className="w-[13px]" />}
        <Icon name={TYPE_ICON[i.type] ?? 'file'} size={15} className="shrink-0 text-slate-400" label={i.type} />
        <button onClick={onToggle} className="min-w-0 flex-1 text-left" aria-expanded={isOpen}>
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-[11px] text-slate-400">{i.key}</span>
            <span className="truncate text-sm font-medium text-slate-800">{i.title}</span>
            {i.epicKey && <Badge icon="layers" title="Epic">{i.epicKey}</Badge>}
            {i.jiraKey && <Badge tone="info" icon="external-link" title="Linked Jira issue">{i.jiraKey}</Badge>}
            {i.releaseId === null && <Badge title="Not assigned to a release yet">Pool</Badge>}
          </div>
        </button>
        <Badge title="Story points">{i.estimate !== null ? `${i.estimate} pts` : 'no estimate'}</Badge>
        <Badge tone={STATUS_TONE[i.status]}>{STATUS_LABEL[i.status]}</Badge>
        {canEdit && <Actions item={i} sprintOpen={sprintOpen} sprintId={sprintId} claimInto={claimInto} run={run} projectId={projectId} onEdit={onToggle} />}
      </div>
      {i.problems.length > 0 && !isOpen && i.status !== 'new' && (
        <div className="ml-8 mt-1 flex items-start gap-1.5 text-xs text-amber-800"><Icon name="warning" size={13} className="mt-0.5 text-amber-600" />Not ready to plan: {i.problems.join('; ')}</div>
      )}
      {isOpen && <Editor item={i} canEdit={canEdit} projectId={projectId} />}
    </li>
  );
}

function Actions({ item: i, sprintOpen, sprintId, claimInto, run, projectId, onEdit }: {
  item: BacklogItem; sprintOpen: boolean; sprintId?: string; claimInto: string | null; run: (fn: () => Promise<unknown>) => void; projectId: string; onEdit: () => void;
}) {
  const ready = i.problems.length === 0;
  const claim = i.releaseId === null && claimInto && !['done', 'dropped', 'in_sprint', 'in_progress'].includes(i.status)
    ? <Button size="sm" variant="ghost" icon="gitbranch" title="Move this item from the shared pool into the release" onClick={() => run(() => agileApi.claim(projectId, claimInto, [i.key]))}>Claim</Button> : null;
  if (i.status === 'dropped') return <Button size="sm" variant="ghost" onClick={() => run(() => agileApi.setStatus(projectId, i.key, 'new', i.version))}>Restore</Button>;
  if (i.status === 'done') return null;
  if (i.status === 'in_sprint') return <Button size="sm" variant="ghost" onClick={() => run(() => agileApi.removeFromSprint(projectId, i.key))}>Remove</Button>;
  if (i.status === 'in_progress') return null;
  if (i.status === 'ready') {
    return (
      <div className="flex gap-1">
        {sprintOpen ? <Button size="sm" variant="primary" icon="plus" onClick={() => {
          run(() => agileApi.addToSprint(projectId, i.key, false, sprintId).catch(async (e: Error) => {
            if (/overcommit/i.test(e.message) && window.confirm(`${e.message}\n\nAdd it anyway?`)) return agileApi.addToSprint(projectId, i.key, true, sprintId);
            throw e;
          }));
        }}>Add to sprint</Button> : null}
        {claim}
        <Button size="sm" variant="ghost" title="Drop from the backlog" onClick={() => run(() => agileApi.setStatus(projectId, i.key, 'dropped', i.version))}>Drop</Button>
      </div>
    );
  }
  return (
    <div className="flex gap-1">
      {ready ? <Button size="sm" variant="secondary" icon="check" onClick={() => run(() => agileApi.setStatus(projectId, i.key, 'ready', i.version))}>Mark ready</Button>
        : <Button size="sm" variant="warning" icon="pencil" onClick={onEdit} title={i.problems.join('; ')}>Fix to make ready</Button>}
      {claim}
      <Button size="sm" variant="ghost" title="Drop from the backlog" onClick={() => run(() => agileApi.setStatus(projectId, i.key, 'dropped', i.version))}>Drop</Button>
    </div>
  );
}

function Editor({ item: i, canEdit, projectId }: { item: BacklogItem; canEdit: boolean; projectId: string }) {
  const [baseVersion] = useState(i.version);            // the version this edit started from: a concurrent change (e.g. Jira) must conflict, not be overwritten
  const [title, setTitle] = useState(i.title);
  const [desc, setDesc] = useState(i.description);
  const [ac, setAc] = useState(i.acceptanceCriteria.join('\n'));
  const [est, setEst] = useState<string>(i.estimate === null ? '' : String(i.estimate));
  const [comps, setComps] = useState(i.components.join(', '));
  const save = useAgileMutation(projectId, () => agileApi.patchItem(projectId, i.key, {
    expectedVersion: baseVersion, title, description: desc,
    acceptanceCriteria: ac.split('\n').map((s) => s.trim()).filter(Boolean),
    ...(est === '' ? { clearEstimate: true } : { estimate: Number(est) }),
    components: comps.split(',').map((s) => s.trim()).filter(Boolean),
  }));
  const dis = !canEdit || i.status === 'dropped';
  return (
    <div className="ml-8 mt-2 space-y-2 rounded-lg border border-slate-200 bg-slate-50 p-3">
      {i.problems.length > 0 && i.status !== 'dropped' && (
        <Callout tone="advice" compact title="To make this ready to plan">{i.problems.join('; ')}.</Callout>)}
      <label className="block text-xs font-semibold text-slate-600">Title
        <input disabled={dis} value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm font-normal" /></label>
      <label className="block text-xs font-semibold text-slate-600">Description
        <textarea disabled={dis} value={desc} onChange={(e) => setDesc(e.target.value)} rows={2} className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm font-normal" /></label>
      <label className="block text-xs font-semibold text-slate-600">Acceptance criteria <span className="font-normal text-slate-400">— one per line, each testable</span>
        <textarea disabled={dis} value={ac} onChange={(e) => setAc(e.target.value)} rows={3} className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm font-normal" /></label>
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="text-xs font-semibold text-slate-600">Estimate (points)
          <select disabled={dis || i.type === 'epic'} value={est} onChange={(e) => setEst(e.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm font-normal">
            <option value="">Not estimated</option>
            {SCALE.map((s) => <option key={s} value={s}>{s}</option>)}
            {i.estimate !== null && !SCALE.includes(i.estimate) && <option value={i.estimate}>{i.estimate}</option>}
          </select></label>
        <label className="text-xs font-semibold text-slate-600">Components <span className="font-normal text-slate-400">— comma separated</span>
          <input disabled={dis} value={comps} onChange={(e) => setComps(e.target.value)} className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm font-normal" /></label>
      </div>
      {save.isError && <Callout tone="error" compact title="Not saved">{errorText(save.error)}</Callout>}
      {canEdit && <div className="flex justify-end"><Button variant="primary" size="sm" icon="check" loading={save.isPending} disabled={!title.trim() || i.status === 'dropped'} onClick={() => save.mutate(undefined)}>Save changes</Button></div>}
    </div>
  );
}
