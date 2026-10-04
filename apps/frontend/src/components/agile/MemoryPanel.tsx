import { useQuery } from '@tanstack/react-query';
import { agileApi } from '../../api/agile';
import { Badge } from '../ui/Badge';
import { Callout } from '../ui/Callout';
import { Icon } from '../ui/Icon';

/** The `.devmind/` project memory: what exists, what is committed, and whether anything drifted. */
export default function MemoryPanel({ projectId }: { projectId: string }) {
  const q = useQuery({ queryKey: ['agile-index', projectId], queryFn: () => agileApi.index(projectId), refetchInterval: 10_000 });
  const s = q.data;
  if (!s) return <div className="p-6 text-sm text-slate-400">Loading project memory…</div>;
  if (!s.initialised) {
    return (
      <div className="m-5">
        <Callout tone="info" title="Project memory starts when the first stage is generated">
          DevMind writes a small, read-only <code>.devmind/</code> folder — charter, sprint digests, release indexes and living specs — and commits it to
          your repository when each stage is approved. Agents read it instead of the whole history, so prompts stay small however long the project runs.
        </Callout>
      </div>
    );
  }
  const un = s.unpublished ?? { added: 0, modified: 0, deleted: 0 };
  const pending = un.added + un.modified + un.deleted;
  return (
    <div className="space-y-4 p-5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge icon="folder">{s.files} files</Badge>
        <Badge>generation {s.generation}</Badge>
        {s.currentRelease && <Badge tone="brand" icon="flag">{s.currentRelease}</Badge>}
        {s.currentSprint && <Badge icon="target">{s.currentSprint}</Badge>}
        <Badge tone={s.strategy === 'index-branch' ? 'info' : 'neutral'} icon="gitbranch">{s.strategy === 'index-branch' ? 'devmind/index branch + PR' : 'default branch'}</Badge>
        {s.publishedCommit && <Badge tone="success" icon="check" title={s.publishedCommit}>committed {s.publishedCommit.slice(0, 7)}</Badge>}
      </div>
      {pending > 0
        ? <Callout tone="advice" compact title={`${pending} change(s) are staged but not committed yet`}>
            They are committed to the repository when the current stage is approved. Nothing reaches the repository before a person approves.</Callout>
        : <Callout tone="success" compact title="Everything is committed">The repository matches DevMind's project memory.</Callout>}
      {(s.drift?.length ?? 0) > 0 && (
        <Callout tone="error" title="Some memory files do not match their recorded hash">
          They were edited or are missing. The files are generated — do not edit them. Regeneration at the next sprint close repairs them.
          <ul className="mt-1 list-disc pl-5 text-xs">{s.drift!.map((d) => <li key={d.path}><code>{d.path}</code> — {d.kind}</li>)}</ul>
        </Callout>)}
      <div className="grid gap-4 sm:grid-cols-2">
        <section className="rounded-xl border border-slate-200 bg-white p-4">
          <h3 className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-slate-700"><Icon name="layers" size={14} />Tiers</h3>
          <p className="mb-2 text-xs text-slate-500">Recent work is loaded into agents; older sprints are rolled up into one file per release.</p>
          <ul className="space-y-1 text-sm">
            {(['active', 'closed', 'archived'] as const).map((t) => (
              <li key={t} className="flex justify-between"><span className="capitalize text-slate-600">{t}</span><span className="font-mono text-slate-800">{s.tiers?.[t] ?? 0}</span></li>))}
          </ul>
        </section>
        <section className="rounded-xl border border-slate-200 bg-white p-4">
          <h3 className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-slate-700"><Icon name="book" size={14} />Living specs</h3>
          {(s.specs?.length ?? 0) === 0 ? <p className="text-xs text-slate-500">No specs yet. They appear when a Build stage with design changes is approved.</p>
            : <ul className="flex flex-wrap gap-1.5">{s.specs!.map((x) => <Badge key={x} icon="file">{x}</Badge>)}</ul>}
        </section>
      </div>
    </div>
  );
}
