import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api/client';
import type { Artefact, PhaseStateView, User } from '../api/types';
import ArtifactViewer from './ArtifactViewer';

interface Props {
  projectId: string;
  pending: PhaseStateView;
  artefacts: Artefact[];
  user: User;
}

interface SignoffStatus {
  phase: number;
  requiredUsers: string[];
  signedUsers: string[];
  remainingUsers: string[];
  artefacts: Array<{ artefactId: string; signedBy: string[] }>;
  complete: boolean;
}

/**
 * HITL Gate Review — artifact-level, per-user sign-off. Every required reviewer
 * must sign off every artifact before the stage completes and the pipeline
 * advances. One reviewer can sign several artifacts; AMEND sends it back.
 */
export default function GatePanel({ projectId, pending, artefacts, user }: Props) {
  const qc = useQueryClient();
  const [amendOpen, setAmendOpen] = useState(false);
  const [comments, setComments] = useState('');
  const [viewArtefactId, setViewArtefactId] = useState<string | null>(null);

  const email = (user.email || '').toLowerCase();
  const isAdmin = user.role === 'SUPER_ADMIN';

  const signoffs = useQuery({
    queryKey: ['signoffs', projectId, pending.phase],
    queryFn: () => api.get<SignoffStatus>(`/api/projects/${projectId}/phase/${pending.phase}/signoffs`),
    refetchInterval: 8000,
  });
  const so = signoffs.data;
  const requiredUsers = so?.requiredUsers ?? [];
  const canSign = isAdmin || requiredUsers.includes(email);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['signoffs', projectId, pending.phase] });
    void qc.invalidateQueries({ queryKey: ['project', projectId] });
    void qc.invalidateQueries({ queryKey: ['artefacts', projectId] });
    void qc.invalidateQueries({ queryKey: ['flow', projectId] });
  };

  const signArtifact = useMutation({
    mutationFn: (artefactId: string) =>
      api.post(`/api/projects/${projectId}/phase/${pending.phase}/artefacts/${artefactId}/signoff`, {}),
    onSuccess: invalidate,
  });
  const review = useMutation({
    mutationFn: (body: { decision: 'APPROVE' | 'AMEND'; comments?: string }) =>
      api.post(`/api/gates/${projectId}/phase/${pending.phase}/review`, body),
    onSuccess: () => { invalidate(); setAmendOpen(false); setComments(''); },
  });

  const phaseArtefacts = artefacts.filter((a) => a.phase === pending.phase);
  const titleFor = (id: string) =>
    id.startsWith('stage:') ? 'Stage sign-off' : (phaseArtefacts.find((a) => a.id === id)?.title ?? id);
  const typeFor = (id: string) => phaseArtefacts.find((a) => a.id === id)?.type ?? 'STAGE';
  const rows = so?.artefacts ?? [];
  const busy = signArtifact.isPending || review.isPending;

  return (
    <div className="rounded-xl border-2 border-amber-300 bg-amber-50 p-4">
      <div className="flex items-center gap-2">
        <span className="text-lg">⏸</span>
        <div className="flex-1">
          <div className="text-sm font-semibold text-amber-900">
            Gate review — Phase {pending.phase}: {pending.name}
          </div>
          <div className="text-xs text-amber-700">
            Every required reviewer must sign off every artifact before the pipeline continues.
          </div>
        </div>
      </div>

      {/* Reviewer roster + overall progress */}
      <div className="mt-3 rounded-lg bg-white p-3">
        <div className="mb-1.5 flex items-center justify-between">
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">
            Required reviewers ({(so?.signedUsers.length ?? 0)}/{requiredUsers.length} fully signed)
          </div>
          {so?.complete
            ? <span className="text-[11px] font-semibold text-emerald-600">All sign-offs complete</span>
            : so?.remainingUsers.length
              ? <span className="text-[11px] text-amber-700">Waiting on {so.remainingUsers.join(', ')}</span>
              : null}
        </div>
        <div className="flex flex-wrap gap-1.5">
          {requiredUsers.length === 0 && (
            <span className="text-[11px] text-slate-400">No reviewers assigned — add project members with the stage's reviewer role, or assign users in the Workflow Designer.</span>
          )}
          {requiredUsers.map((u) => {
            const fully = (so?.signedUsers ?? []).includes(u);
            return (
              <span key={u}
                className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${
                  fully ? 'bg-emerald-50 text-emerald-700' : 'border border-amber-300 bg-amber-50 text-amber-700'}`}>
                {fully ? '✓' : '○'} {u}{u === email ? ' (you)' : ''}
              </span>
            );
          })}
        </div>
      </div>

      {/* Per-artifact sign-off */}
      <div className="mt-3 rounded-lg bg-white p-3">
        <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">
          Artifacts — sign off each one
        </div>
        <ul className="space-y-1.5">
          {rows.map((r) => {
            const signedByMe = r.signedBy.includes(email);
            const isStageRow = r.artefactId.startsWith('stage:');
            return (
              <li key={r.artefactId} className="flex items-center gap-2 text-sm">
                <span className="rounded bg-brand-50 px-1.5 py-0.5 text-[10px] font-semibold text-brand-700">{typeFor(r.artefactId)}</span>
                {isStageRow ? (
                  <span className="min-w-0 flex-1 truncate text-slate-600">{titleFor(r.artefactId)}</span>
                ) : (
                  <button onClick={() => setViewArtefactId(r.artefactId)} className="min-w-0 flex-1 truncate text-left text-brand-700 hover:underline">
                    {titleFor(r.artefactId)}
                  </button>
                )}
                <span className="shrink-0 text-[10px] text-slate-400">
                  {r.signedBy.length ? `signed: ${r.signedBy.map((e) => e.split('@')[0]).join(', ')}` : 'unsigned'}
                </span>
                {canSign && (
                  signedByMe ? (
                    <span className="shrink-0 rounded bg-emerald-50 px-2 py-0.5 text-[10px] font-semibold text-emerald-700">✓ you signed</span>
                  ) : (
                    <button
                      onClick={() => signArtifact.mutate(r.artefactId)}
                      disabled={busy}
                      className="shrink-0 rounded bg-emerald-600 px-2 py-0.5 text-[10px] font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
                    >
                      Sign off
                    </button>
                  )
                )}
              </li>
            );
          })}
        </ul>
      </div>

      {viewArtefactId && (
        <ArtifactViewer projectId={projectId} artefactId={viewArtefactId} onClose={() => setViewArtefactId(null)} canRepair={canSign} />
      )}

      {/* Amend (send back) + admin override */}
      <div className="mt-3">
        {canSign ? (
          !amendOpen ? (
            <div className="flex flex-wrap gap-2">
              <button
                onClick={() => setAmendOpen(true)}
                disabled={busy}
                className="rounded-lg bg-orange-500 px-4 py-2 text-sm font-semibold text-white hover:bg-orange-600 disabled:opacity-50"
              >
                ↺ Request amendments
              </button>
              {isAdmin && !so?.complete && (
                <button
                  onClick={() => review.mutate({ decision: 'APPROVE' })}
                  disabled={busy}
                  className="rounded-lg border border-red-300 px-4 py-2 text-sm font-semibold text-red-700 hover:bg-red-50 disabled:opacity-50"
                  title="Super-admin override — force-complete the gate without every sign-off (audited)."
                >
                  ⚡ Override &amp; complete
                </button>
              )}
            </div>
          ) : (
            <div className="space-y-2">
              <textarea
                className="w-full rounded-lg border border-slate-300 p-2 text-sm focus:border-brand-500 focus:outline-none"
                rows={3}
                placeholder='Specific feedback for the agent, e.g. "Add a caching layer to the LLD"'
                value={comments}
                onChange={(e) => setComments(e.target.value)}
              />
              <div className="flex gap-2">
                <button
                  onClick={() => review.mutate({ decision: 'AMEND', comments })}
                  disabled={review.isPending || comments.trim().length === 0}
                  className="rounded-lg bg-orange-500 px-4 py-2 text-sm font-semibold text-white hover:bg-orange-600 disabled:opacity-50"
                >
                  Send feedback — regenerate
                </button>
                <button onClick={() => setAmendOpen(false)} className="rounded-lg px-3 py-2 text-sm text-slate-600 hover:bg-slate-100">
                  Cancel
                </button>
              </div>
              <div className="text-[11px] text-amber-700">Requesting changes clears all sign-offs — every reviewer signs the new version.</div>
            </div>
          )
        ) : (
          <div className="rounded-lg bg-white px-3 py-2 text-xs text-slate-500">
            You are not an assigned reviewer for this stage. Its required reviewers must sign off each artifact before it advances.
          </div>
        )}
        {(signArtifact.isError || review.isError) && (
          <div className="mt-2 rounded bg-red-50 px-3 py-2 text-xs text-red-700">
            {(signArtifact.error || review.error) instanceof Error
              ? (signArtifact.error || review.error as Error).message
              : 'Action failed'}
          </div>
        )}
      </div>
    </div>
  );
}
