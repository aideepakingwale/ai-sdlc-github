import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';
import type { ProjectFlow } from '../api/flow';
import type { DirectoryUser, PhaseRole, ProjectMember } from '../api/types';
import { ROLE_LABELS } from '../api/types';
import { Pill, initials } from './bits';

const PHASE_ROLES: PhaseRole[] = ['PO', 'SA', 'TA', 'QA', 'DEVOPS', 'DEV'];

/** Who is on the project and which stage each person covers; managers can add and remove people. */
export default function ProjectTeam({ projectId, flow, canManage }: { projectId: string; flow: ProjectFlow | undefined; canManage: boolean }) {
  const qc = useQueryClient();
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<PhaseRole>('PO');
  const [error, setError] = useState('');
  const members = useQuery({ queryKey: ['members', projectId], queryFn: () => api.get<{ members: ProjectMember[] }>(`/api/projects/${projectId}/members`) });
  const directory = useQuery({ queryKey: ['users'], queryFn: () => api.get<{ users: DirectoryUser[] }>('/api/users'), enabled: canManage, staleTime: 60_000 });
  const done = () => { setError(''); void qc.invalidateQueries({ queryKey: ['members', projectId] }); void qc.invalidateQueries({ queryKey: ['project', projectId] }); };
  const add = useMutation({
    mutationFn: () => api.post(`/api/projects/${projectId}/members`, { email, role }),
    onSuccess: () => { setEmail(''); done(); },
    onError: (e) => setError(e instanceof Error ? e.message : 'Could not add this person'),
  });
  const remove = useMutation({
    mutationFn: (userId: string) => fetch(`/api/projects/${projectId}/members/${userId}`, { method: 'DELETE', credentials: 'include' }).then((r) => { if (!r.ok) throw new Error('Could not remove this person'); }),
    onSuccess: done,
    onError: (e) => setError(e instanceof Error ? e.message : 'Could not remove this person'),
  });
  const list = members.data?.members ?? [];
  const stageFor = (r: string) => flow?.stages.find((s) => s.reviewerRole === r || s.team.includes(r))?.name ?? '';
  const covered = new Set(list.map((m) => m.role)).size;
  const candidates = (directory.data?.users ?? []).filter((u) => u.role === role);
  return (
    <div data-testid="v2-team-tab">
      <h3 className="mb-2 text-sm font-semibold text-navy">{list.length} {list.length === 1 ? 'person' : 'people'} · {covered}/{PHASE_ROLES.length} stage roles covered</h3>
      {list.length === 0 ? (
        <div className="rounded-lg bg-slate-100 p-4 text-center text-sm text-slate-500">No one yet{canManage ? '. Add one person per stage role below.' : '.'}</div>
      ) : (
        <ul className="divide-y divide-slate-200 rounded-xl border border-slate-200 bg-white">
          {list.map((m) => (
            <li key={m.userId} className="flex items-center gap-3 px-3 py-2.5">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-600 text-[11px] font-bold text-white" aria-hidden="true">{initials(m.displayName)}</span>
              <div className="min-w-0 flex-1"><div className="truncate text-sm font-medium text-slate-900">{m.displayName}</div><div className="truncate text-xs text-slate-500">{m.email}</div></div>
              <Pill tone="brand">{m.role}</Pill>
              <span className="hidden text-xs text-slate-500 sm:inline">{stageFor(m.role)}</span>
              {canManage && <button type="button" onClick={() => remove.mutate(m.userId)} aria-label={`Remove ${m.displayName}`} className="rounded-lg border border-slate-300 px-2 py-0.5 text-xs text-slate-500 hover:border-bared-500 hover:text-bared-600">✕</button>}
            </li>
          ))}
        </ul>
      )}
      {canManage && (
        <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-4">
          <h3 className="mb-3 text-sm font-semibold text-navy">Add a member</h3>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-xs text-slate-500">Stage role
              <select value={role} onChange={(e) => setRole(e.target.value as PhaseRole)} className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-900">
                {PHASE_ROLES.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
              </select>
            </label>
            <label className="text-xs text-slate-500">Person
              <input list="v2-team-candidates" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@company.com" className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-900" />
              <datalist id="v2-team-candidates">{candidates.map((u) => <option key={u.id} value={u.email}>{u.displayName}</option>)}</datalist>
            </label>
          </div>
          <button type="button" onClick={() => add.mutate()} disabled={add.isPending || !email.includes('@')} className="mt-3 rounded-lg bg-brand-600 px-4 py-1.5 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-40">{add.isPending ? 'Adding…' : 'Add to team'}</button>
          {error && <p className="mt-2 rounded bg-bared-200 px-2 py-1 text-xs text-bared-700">{error}</p>}
          <p className="mt-2 text-xs text-slate-500">The role must match the person’s platform role. This is enforced on the server.</p>
        </div>
      )}
    </div>
  );
}
