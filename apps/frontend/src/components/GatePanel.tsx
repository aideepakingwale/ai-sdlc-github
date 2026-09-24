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
  reviewers: string[];        // users authorised to sign off
  signedUsers: string[];      // who has signed so far
  signedCount: number;
  totalCount: number;
  unsignedArtefacts: string[];
  artefacts: Array<{ artefactId: string; signedBy: string[] }>;
  complete: boolean;
}

/**
 * HITL Gate Review — document-coverage sign-off. Every document must be signed off
 * by an authorised reviewer; one reviewer can sign several documents. The stage
 * completes once ALL documents are signed. AMEND sends it back and clears sign-offs.
 */
export default function GatePanel({ projectId, pending, artefacts, user }: Props) {
  const qc = useQueryClient();
  const [amendOpen, setAmendOpen] = useState(false);
  const [comments, setComments] = useState('');
  const [viewArtefactId, setViewArtefactId] = useState<string | null>(null);
  const [newReviewer, setNewReviewer] = useState('');

  const email = (user.email || '').toLowerCase();
  const isAdmin = user.role === 'SUPER_ADMIN';
  const canManageReviewers = isAdmin || user.role === 'PROJECT_MANAGER';

  const signoffs = useQuery({
    queryKey: ['signoffs', projectId, pending.phase],
    queryFn: () => api.get<SignoffStatus>(`/api/projects/${projectId}/phase/${pending.phase}/signoffs`),
    refetchInterval: 8000,
  });
  const so = signoffs.data;
  const reviewers = so?.reviewers ?? [];
  const canSign = isAdmin || reviewers.includes(email);

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
  const setReviewers = useMutation({
    mutationFn: (users: string[]) =>
      api.put(`/api/projects/${projectId}/phase/${pending.phase}/reviewers`, { users }),
    onSuccess: invalidate,
  });

  const phaseArtefacts = artefacts.filter((a) => a.phase === pending.phase);
  const titleFor = (id: string) =>
    id.startsWith('stage:') ? 'Stage sign-off' : (phaseArtefacts.find((a) => a.id === id)?.title ?? id);
  const typeFor = (id: string) => phaseArtefacts.find((a) => a.id === id)?.type ?? 'STAGE';
  const rows = so?.artefacts ?? [];
  const busy = signArtifact.isPending || review.isPending || setReviewers.isPending;
  // Matrix columns = authorised reviewers, plus the current user if they can sign
  // (e.g. an admin acting via override) so they always have a column to click.
  const columns = [...reviewers];
  if (canSign && email && !columns.includes(email)) columns.push(email);
  const short = (e: string) => e.split('@')[0];
  const addReviewer = () => {
    const e = newReviewer.trim().toLowerCase();
    if (!e || reviewers.includes(e)) { setNewReviewer(''); return; }
    setReviewers.mutate([...reviewers, e]);
    setNewReviewer('');
  };

  return (
    <div className="rounded-xl border-2 border-amber-300 bg-amber-50 p-4">
      <div className="flex items-center gap-2">
        <span className="text-lg">⏸</span>
        <div className="flex-1">
          <div className="text-sm font-semibold text-amber-900">
            Gate review — Phase {pending.phase}: {pending.name}
          </div>
          <div className="text-xs text-amber-700">
            Every document must be signed off before the pipeline continues. One reviewer can sign several documents.
          </div>
        </div>
      </div>

      {/* Sign-off matrix: documents (rows) × reviewers (columns) */}
      <div className="mt-3 rounded-lg bg-white p-3">
        <div className="mb-2 flex items-center justify-between">
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">
            Sign-off matrix — documents signed off ({so?.signedCount ?? 0}/{so?.totalCount ?? 0})
          </div>
          {so?.complete
            ? <span className="text-[11px] font-semibold text-emerald-600">✓ All documents signed</span>
            : (so && so.totalCount - so.signedCount > 0)
              ? <span className="text-[11px] text-amber-700">{so.totalCount - so.signedCount} awaiting sign-off</span>
              : null}
        </div>

        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr>
                <th className="sticky left-0 z-10 bg-white px-2 py-1.5 text-left text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                  Document
                </th>
                {columns.map((u) => (
                  <th key={u} className="px-2 py-1.5 text-center align-bottom">
                    <div className="flex flex-col items-center gap-0.5">
                      <span className={`max-w-[90px] truncate text-[11px] font-semibold ${u === email ? 'text-brand-700' : 'text-slate-600'}`} title={u}>
                        {short(u)}{u === email ? ' (you)' : ''}
                      </span>
                      {canManageReviewers && u !== email && (
                        <button
                          onClick={() => setReviewers.mutate(reviewers.filter((r) => r !== u))}
                          disabled={busy}
                          className="text-[9px] text-slate-300 hover:text-bared-600"
                          title="Remove reviewer"
                        >✕ remove</button>
                      )}
                    </div>
                  </th>
                ))}
                {columns.length === 0 && (
                  <th className="px-2 py-1.5 text-[11px] font-normal text-slate-400">No reviewers yet — add one below.</th>
                )}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const isStageRow = r.artefactId.startsWith('stage:');
                const covered = r.signedBy.length > 0;
                return (
                  <tr key={r.artefactId} className="border-t border-slate-100">
                    <td className="sticky left-0 z-10 bg-white px-2 py-1.5">
                      <div className="flex items-center gap-1.5">
                        <span className={covered ? 'text-emerald-500' : 'text-slate-300'}>{covered ? '✓' : '○'}</span>
                        <span className="rounded bg-brand-50 px-1 py-0.5 text-[9px] font-semibold text-brand-700">{typeFor(r.artefactId)}</span>
                        {isStageRow ? (
                          <span className="max-w-[220px] truncate text-xs text-slate-600">{titleFor(r.artefactId)}</span>
                        ) : (
                          <button onClick={() => setViewArtefactId(r.artefactId)} className="max-w-[220px] truncate text-left text-xs text-brand-700 hover:underline" title={titleFor(r.artefactId)}>
                            {titleFor(r.artefactId)}
                          </button>
                        )}
                      </div>
                    </td>
                    {columns.map((u) => {
                      const signed = r.signedBy.includes(u);
                      const mine = u === email;
                      const clickable = mine && canSign && !signed;
                      return (
                        <td key={u} className="px-2 py-1.5 text-center">
                          {signed ? (
                            <span className="text-emerald-600" title={`Signed by ${u}`}>✓</span>
                          ) : clickable ? (
                            <button
                              onClick={() => signArtifact.mutate(r.artefactId)}
                              disabled={busy}
                              className="mx-auto block h-4 w-4 rounded border border-slate-300 hover:border-emerald-500 hover:bg-emerald-50 disabled:opacity-50"
                              title="Click to sign off this document"
                            />
                          ) : (
                            <span className="text-slate-200">·</span>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {canManageReviewers && (
          <div className="mt-2 flex items-center gap-2">
            <input
              className="w-64 rounded border border-slate-300 px-2 py-1 text-xs focus:border-brand-500 focus:outline-none"
              placeholder="Add reviewer email…"
              value={newReviewer}
              onChange={(e) => setNewReviewer(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') addReviewer(); }}
            />
            <button
              onClick={addReviewer}
              disabled={busy || !newReviewer.trim()}
              className="rounded bg-brand-600 px-3 py-1 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-40"
            >
              + Add reviewer
            </button>
            {setReviewers.isError && (
              <span className="text-[11px] text-bared-600">
                {setReviewers.error instanceof Error ? setReviewers.error.message : 'Failed to update reviewers'}
              </span>
            )}
          </div>
        )}
        <div className="mt-1.5 text-[10px] text-slate-400">
          Any authorised reviewer can sign any document, and one reviewer can sign several. The stage completes once every document has a ✓.
        </div>
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
