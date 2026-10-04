import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { agileApi, type AgileOverview, type CarrySuggestions } from '../../api/agile';
import { CARRY_STATE_LABEL, CARRY_STATE_TONE, KIND_LABEL } from '../../lib/agileView';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Callout } from '../ui/Callout';
import ReleasePolicy from './ReleasePolicy';
import { errorText, useAgileMutation } from './hooks';

const PRESET_LABEL: Record<string, string> = { inherit: 'Same stages as the project', lean: 'No planning ceremony', hotfix: 'Fix and ship', 'build-only': 'Build and review only', custom: 'Custom' };

/** One release's lineage and context: where it came from, what it carried, how new work reaches it. */
export default function ReleaseContext({ projectId, overview }: { projectId: string; overview: AgileOverview }) {
  const rel = overview.currentRelease;
  const canRun = overview.permissions.canRun;
  const carry = useQuery({ queryKey: ['agile-carry', projectId, rel?.id], queryFn: () => agileApi.carry(projectId, rel!.id), enabled: Boolean(rel) });
  const epics = useQuery({ queryKey: ['agile-release-epics', projectId, rel?.id], queryFn: () => agileApi.releaseEpics(projectId, rel!.id), enabled: Boolean(rel) && rel?.intakeRule === 'epic' });
  const [adding, setAdding] = useState<CarrySuggestions | null>(null);
  const [pick, setPick] = useState<string[]>([]);
  const suggest = useAgileMutation(projectId, async () => agileApi.suggestCarry(projectId, rel!.forkedFromId!, { scopeText: `${rel!.name}. ${rel!.goal}` }));
  const extend = useAgileMutation(projectId, async () => agileApi.extendCarry(projectId, rel!.id, pick));
  const unmap = useAgileMutation(projectId, (epic: string) => agileApi.unmapEpic(projectId, epic));
  const resume = useAgileMutation(projectId, () => agileApi.startRelease(projectId, { ...rel!.setupAnswers!, resumeReleaseId: rel!.id }));
  if (!rel) return <div className="p-6 text-sm text-slate-500">No release is selected.</div>;
  const c = carry.data;
  const carried = new Set((c?.carried ?? []).map((x) => x.id));
  return (
    <div className="space-y-4 p-5">
      {rel.setupComplete === false && (
        <Callout tone="warning" title="Starting this release stopped part-way"
          action={canRun && rel.setupAnswers ? <Button size="sm" variant="primary" icon="play" loading={resume.isPending} onClick={() => resume.mutate(undefined)}>Resume setup</Button> : undefined}>
          The release exists, but some of its setup (context, items or epics) did not finish. Resuming repeats only the steps that are missing.
          {resume.isError && <span className="mt-1 block font-semibold text-red-700">{errorText(resume.error)}</span>}
        </Callout>)}
      <section aria-label="Lineage" className="rounded-xl border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-semibold text-slate-800">{rel.code} · {rel.name}</h3>
          <Badge tone={rel.status === 'closed' ? 'success' : rel.status === 'hardening' ? 'warning' : 'info'}>{rel.status}</Badge>
          <Badge>{PRESET_LABEL[rel.stagePreset ?? 'inherit'] ?? rel.stagePreset}</Badge>
          <Badge title="Where new Jira issues go">{rel.intakeRule === 'epic' ? 'New issues by epic' : 'New issues to the shared pool'}</Badge>
          <Badge>{rel.usePool === false ? 'Does not draw on the pool' : 'May draw on the pool'}</Badge>
        </div>
        {rel.goal && <p className="mt-2 text-sm text-slate-600">{rel.goal}</p>}
        <p className="mt-2 text-xs text-slate-500">{rel.forkedFrom
          ? <>Forked from <strong>{rel.forkedFrom}</strong>{rel.forkBaseline?.sprint ? ` at ${rel.forkBaseline.sprint}` : ''}. It connects back for reference only: nothing is merged back, and either release can change on its own.</>
          : 'Started blank — it does not build on another release.'}</p>
      </section>

      {rel.intakeRule === 'epic' && (
        <section aria-label="Epics" className="rounded-xl border border-slate-200 bg-white p-4">
          <h3 className="mb-2 text-sm font-semibold text-slate-800">Epics routed to this release</h3>
          {(epics.data?.epics ?? []).length === 0 ? <p className="text-xs text-slate-500">No epics are mapped yet. New Jira issues under a mapped epic land here; everything else goes to the shared pool.</p>
            : <ul className="space-y-1">{epics.data!.epics.map((e) => (
              <li key={e.epicKey} className="flex items-center gap-2 text-sm"><span className="text-xs text-slate-400">{e.epicKey}</span>{e.title}
                {canRun && <Button size="sm" variant="ghost" className="ml-auto" onClick={() => unmap.mutate(e.epicKey)}>Unmap</Button>}</li>))}</ul>}
        </section>)}

      <section aria-label="Carried context" className="rounded-xl border border-slate-200 bg-white p-4">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-semibold text-slate-800">Context</h3>
          {c && (['carried', 'modified', 'new', 'retired'] as const).map((s) => (c.counts[s] ?? 0) > 0 && <Badge key={s} tone={CARRY_STATE_TONE[s]}>{c.counts[s]} {CARRY_STATE_LABEL[s].toLowerCase()}</Badge>)}
          {canRun && rel.forkedFromId && rel.status !== 'closed' && <Button size="sm" className="ml-auto" icon="plus" loading={suggest.isPending}
            onClick={() => suggest.mutate(undefined, { onSuccess: (r) => { setAdding(r); setPick([]); } })}>Add context</Button>}
        </div>
        {carry.isError && <Callout tone="error" compact title="Could not load the context">{errorText(carry.error)}</Callout>}
        {c && c.sourceChanged.length > 0 && <Callout tone="advice" compact title="The source release has changed">{c.sourceChanged.length} carried entr{c.sourceChanged.length === 1 ? 'y has' : 'ies have'} changed in {c.forkedFrom} since the fork. Nothing is updated automatically; review them if they matter here.</Callout>}
        {c && c.carried.length + c.new.length === 0 && <p className="text-xs text-slate-500">{rel.forkedFrom ? 'Nothing was carried over, and nothing new has been designed yet.' : 'No design sections yet. They appear as sprints change the design.'}</p>}
        <ul className="divide-y divide-slate-100">
          {[...(c?.carried ?? []), ...(c?.new ?? [])].map((x) => (
            <li key={x.id} className="flex items-center gap-2 py-1.5 text-sm">
              <Badge tone={CARRY_STATE_TONE[x.state]}>{CARRY_STATE_LABEL[x.state]}</Badge>
              <span className="text-xs text-slate-400">{KIND_LABEL[x.kind]}</span>
              <span className="min-w-0 truncate text-slate-800">{x.title}</span>
            </li>))}
        </ul>
        {adding && (
          <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
            <p className="mb-2 text-xs font-semibold text-slate-600">Add from {rel.forkedFrom} <Badge tone={adding.source === 'ai' ? 'brand' : 'neutral'}>{adding.source === 'ai' ? 'AI-ranked' : 'keyword-ranked'}</Badge></p>
            <div className="max-h-48 space-y-1 overflow-auto">
              {adding.candidates.filter((x) => !carried.has(x.id)).map((x) => (
                <label key={x.id} className="flex items-center gap-2 text-sm">
                  <input type="checkbox" disabled={x.tooLarge} checked={pick.includes(x.id)} onChange={() => setPick(pick.includes(x.id) ? pick.filter((i) => i !== x.id) : [...pick, x.id])} />
                  <span className="text-xs text-slate-400">{KIND_LABEL[x.kind]}</span>{x.title}
                  {adding.suggestions.some((s) => s.id === x.id) && <Badge tone="success">suggested</Badge>}</label>))}
            </div>
            <div className="mt-2 flex gap-2">
              <Button size="sm" variant="primary" disabled={!pick.length} loading={extend.isPending}
                onClick={() => extend.mutate(undefined, { onSuccess: () => { setAdding(null); void carry.refetch(); } })}>Add {pick.length || ''} to this release</Button>
              <Button size="sm" variant="ghost" onClick={() => setAdding(null)}>Cancel</Button>
            </div>
            {extend.isError && <Callout tone="error" compact title="Could not add">{errorText(extend.error)}</Callout>}
          </div>)}
        {suggest.isError && <Callout tone="error" compact title="No suggestions">{errorText(suggest.error)}</Callout>}
      </section>
      {overview.permissions.canManage && <ReleasePolicy projectId={projectId} overview={overview} />}
    </div>
  );
}
