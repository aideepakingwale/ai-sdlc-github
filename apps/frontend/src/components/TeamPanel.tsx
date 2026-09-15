import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';
import type { DirectoryUser, PhaseRole, ProjectMember } from '../api/types';
import { ROLE_LABELS } from '../api/types';

const PHASE_ROLES: PhaseRole[] = ['PO', 'SA', 'TA', 'QA', 'DEVOPS', 'DEV'];

/** Team management: PM/SUPER_ADMIN assign phase-role members per project. */
export default function TeamPanel({ projectId, canManage }: { projectId: string; canManage: boolean }) {
  const qc = useQueryClient();
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<PhaseRole>('PO');
  const [error, setError] = useState('');

  const members = useQuery({
    queryKey: ['members', projectId],
    queryFn: () => api.get<{ members: ProjectMember[] }>(`/api/projects/${projectId}/members`),
  });

  const directory = useQuery({
    queryKey: ['users'],
    queryFn: () => api.get<{ users: DirectoryUser[] }>('/api/users'),
    enabled: canManage,
    staleTime: 60_000,
  });

  const invalidate = () => {
    setError('');
    void qc.invalidateQueries({ queryKey: ['members', projectId] });
    void qc.invalidateQueries({ queryKey: ['project', projectId] });
  };

  const add = useMutation({
    mutationFn: () => api.post(`/api/projects/${projectId}/members`, { email, role }),
    onSuccess: () => {
      setEmail('');
      invalidate();
    },
    onError: (e) => setError(e instanceof Error ? e.message : 'Failed to add member'),
  });

  const remove = useMutation({
    mutationFn: (userId: string) =>
      fetch(`/api/projects/${projectId}/members/${userId}`, { method: 'DELETE', credentials: 'include' }).then((r) => {
        if (!r.ok) throw new Error('Remove failed');
      }),
    onSuccess: invalidate,
    onError: (e) => setError(e instanceof Error ? e.message : 'Failed to remove member'),
  });

  // Suggest directory users whose platform role matches the selected phase role.
  const candidates = (directory.data?.users ?? []).filter((u) => u.role === role);

  return (
    <div className="space-y-3">
      <div>
        <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">
          Team ({members.data?.members.length ?? 0}/6 phase roles)
        </div>
        <div className="space-y-1.5">
          {(members.data?.members ?? []).map((m) => (
            <div key={m.userId} className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white p-2">
              <span className="rounded bg-brand-50 px-1.5 py-0.5 text-[10px] font-bold text-brand-700">{m.role}</span>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium text-slate-800">{m.displayName}</div>
                <div className="truncate text-[11px] text-slate-400">{m.email}</div>
              </div>
              {canManage && (
                <button
                  onClick={() => remove.mutate(m.userId)}
                  className="rounded px-1.5 py-0.5 text-xs text-slate-400 hover:bg-red-50 hover:text-red-600"
                  title="Remove from project"
                >
                  ✕
                </button>
              )}
            </div>
          ))}
          {(members.data?.members ?? []).length === 0 && (
            <div className="rounded-lg bg-slate-100 p-3 text-center text-xs text-slate-500">
              No team members yet{canManage ? ' — assign one per phase below.' : '.'}
            </div>
          )}
        </div>
      </div>

      {canManage && (
        <div className="rounded-lg border border-slate-200 bg-white p-2.5">
          <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">Add member</div>
          <select
            className="mb-1.5 w-full rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
            value={role}
            onChange={(e) => setRole(e.target.value as PhaseRole)}
          >
            {PHASE_ROLES.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABELS[r]}
              </option>
            ))}
          </select>
          <input
            className="mb-1.5 w-full rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
            placeholder="email@sdlc.local"
            list="team-candidates"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <datalist id="team-candidates">
            {candidates.map((u) => (
              <option key={u.id} value={u.email}>
                {u.displayName}
              </option>
            ))}
          </datalist>
          <button
            onClick={() => add.mutate()}
            disabled={add.isPending || !email.includes('@')}
            className="w-full rounded-lg bg-brand-600 py-1.5 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-40"
          >
            {add.isPending ? 'Adding…' : 'Add to team'}
          </button>
          {error && <div className="mt-1.5 rounded bg-red-50 px-2 py-1 text-xs text-red-700">{error}</div>}
          <div className="mt-1.5 text-[10px] text-slate-400">
            Membership role must match the user's platform role (enforced server-side).
          </div>
        </div>
      )}
    </div>
  );
}
