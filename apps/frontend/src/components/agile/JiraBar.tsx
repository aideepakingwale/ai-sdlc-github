import { useQuery } from '@tanstack/react-query';
import { agileApi } from '../../api/agile';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Callout } from '../ui/Callout';
import { errorText, useAgileMutation } from './hooks';

/** Jira link state + a manual sync. Silent when the project has no Jira key. */
export default function JiraBar({ projectId, canSync }: { projectId: string; canSync: boolean }) {
  const st = useQuery({ queryKey: ['agile-jira', projectId], queryFn: () => agileApi.jira(projectId), refetchInterval: 20_000 });
  const sync = useAgileMutation(projectId, (full: boolean) => agileApi.jiraSync(projectId, full));
  if (!st.data?.enabled) return null;
  const s = st.data;
  const failed = s.lastStatus === 'error';
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600">
        <Badge tone="info" icon="external-link">Jira {s.projectKey}</Badge>
        <span>{s.lastRunAt ? `Last sync ${new Date(s.lastRunAt).toLocaleString()}` : 'Not synced yet'}</span>
        {s.lastStatus && <Badge tone={failed ? 'error' : s.lastStatus === 'partial' ? 'warning' : 'success'}>{s.lastStatus}</Badge>}
        {(s.stats?.conflicts ?? 0) > 0 && <Badge tone="warning" title="Edited in both places — Jira's version was kept">{s.stats!.conflicts} conflict(s)</Badge>}
        {canSync && <Button size="sm" className="ml-auto" icon="refresh" loading={sync.isPending} onClick={() => { sync.mutate(false); void st.refetch(); }}>Sync now</Button>}
      </div>
      {failed && <Callout tone="error" compact title="The last Jira sync failed">{s.lastError ?? 'Check the Jira connection.'} Your backlog is unchanged.</Callout>}
      {sync.isError && <Callout tone="error" compact title="Sync did not run">{errorText(sync.error)}</Callout>}
    </div>
  );
}
