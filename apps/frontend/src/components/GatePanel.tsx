import { useMutation, useQueryClient } from '@tanstack/react-query';
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

/** HITL Gate Review panel (Module 7 §2B): role-gated Approve / Amend controls. */
export default function GatePanel({ projectId, pending, artefacts, user }: Props) {
  const qc = useQueryClient();
  const [amendOpen, setAmendOpen] = useState(false);
  const [comments, setComments] = useState('');
  const [viewArtefactId, setViewArtefactId] = useState<string | null>(null);
  // Server-computed: membership in THIS project with the matching phase
  // role, or SUPER_ADMIN break-glass. Platform role alone is not enough.
  const canReview = pending.canReview;
  const isOverride = canReview && user.role === 'SUPER_ADMIN';

  const review = useMutation({
    mutationFn: (body: { decision: 'APPROVE' | 'AMEND'; comments?: string }) =>
      api.post(`/api/gates/${projectId}/phase/${pending.phase}/review`, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['project', projectId] });
      void qc.invalidateQueries({ queryKey: ['artefacts', projectId] });
      setAmendOpen(false);
      setComments('');
    },
  });

  const phaseArtefacts = artefacts.filter((a) => a.phase === pending.phase);

  return (
    <div className="rounded-xl border-2 border-amber-300 bg-amber-50 p-4">
      <div className="flex items-center gap-2">
        <span className="text-lg">⏸</span>
        <div className="flex-1">
          <div className="text-sm font-semibold text-amber-900">
            Gate review — Phase {pending.phase}: {pending.name}
          </div>
          <div className="text-xs text-amber-700">
            Requires {pending.reviewerRole} (or ADMIN) sign-off before the pipeline continues.
          </div>
        </div>
      </div>

      {phaseArtefacts.length > 0 && (
        <div className="mt-3 rounded-lg bg-white p-3">
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">
            Review checklist — artifacts in this phase (click to open in the viewer)
          </div>
          <ul className="space-y-1">
            {phaseArtefacts.map((a) => (
              <li key={a.id} className="flex items-center gap-2 text-sm text-slate-700">
                <span className="rounded bg-brand-50 px-1.5 py-0.5 text-[10px] font-semibold text-brand-700">{a.type}</span>
                <button
                  onClick={() => setViewArtefactId(a.id)}
                  className="min-w-0 truncate text-left text-brand-700 hover:underline"
                  title="Open in the type-aware viewer (diagram/markdown/code)"
                >
                  {a.title}
                </button>
                <span className="ml-auto flex shrink-0 items-center gap-1.5">
                  <a
                    href={`/api/projects/${projectId}/artefacts/${a.id}/download`}
                    download
                    className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold text-slate-500 hover:bg-brand-100 hover:text-brand-700"
                    title="Download — opens with the application installed on your desktop"
                  >
                    ⬇
                  </a>
                  {a.url && (
                    <a
                      href={a.url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-[10px] text-slate-400 hover:text-brand-600"
                      title="Open in the external tool"
                    >
                      ↗
                    </a>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {viewArtefactId && (
        <ArtifactViewer projectId={projectId} artefactId={viewArtefactId} onClose={() => setViewArtefactId(null)} canRepair={canReview} />
      )}

      {canReview ? (
        <div className="mt-3">
          {isOverride && (
            <div className="mb-2 rounded-lg bg-red-50 px-3 py-1.5 text-xs font-medium text-red-700">
              ⚡ Super-admin override — this bypasses the {pending.reviewerRole} team member and is logged in the audit trail.
            </div>
          )}
          {!amendOpen ? (
            <div className="flex gap-2">
              <button
                onClick={() => review.mutate({ decision: 'APPROVE' })}
                disabled={review.isPending}
                className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
              >
                ✓ Approve — advance pipeline
              </button>
              <button
                onClick={() => setAmendOpen(true)}
                disabled={review.isPending}
                className="rounded-lg bg-orange-500 px-4 py-2 text-sm font-semibold text-white hover:bg-orange-600 disabled:opacity-50"
              >
                ↺ Request amendments
              </button>
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
            </div>
          )}
          {review.isError && (
            <div className="mt-2 rounded bg-red-50 px-3 py-2 text-xs text-red-700">
              {review.error instanceof Error ? review.error.message : 'Review failed'}
            </div>
          )}
          {review.isPending && <div className="mt-2 text-xs text-amber-700">Submitting decision…</div>}
        </div>
      ) : (
        <div className="mt-3 rounded-lg bg-white px-3 py-2 text-xs text-slate-500">
          {user.role === 'PROJECT_MANAGER'
            ? 'Project Managers manage the team but never approve gates (segregation of duties).'
            : `You are ${user.role} — this gate needs the project's ${pending.reviewerRole} team member. The chat stays locked until sign-off.`}
        </div>
      )}
    </div>
  );
}
