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

interface Cell { assigned: string[]; signed: string[] }
interface Row { kind: 'stage' | 'type' | 'artifact'; target: string; label?: string; type?: string; artefactId?: string }
interface SignoffStatus {
  phase: number;
  reviewers: string[];
  outputTypes: string[];
  rows: Row[];
  cells: Record<string, Cell>;
  reviewedArtefacts: string[];
  signedCount: number;
  totalCount: number;
  complete: boolean;
}

/**
 * HITL Gate Review — review matrix. Reviewer users (columns) are assigned to review
 * targets (rows): the ENTIRE STAGE, an OUTPUT TYPE, or a specific ARTIFACT. The PM
 * marks assignments; each reviewer signs off what they're assigned to. The stage
 * completes when every artifact is covered and every assignment is signed.
 */
export default function GatePanel({ projectId, pending, artefacts, user }: Props) {
  const qc = useQueryClient();
  const [amendOpen, setAmendOpen] = useState(false);
  const [comments, setComments] = useState('');
  const [viewArtefactId, setViewArtefactId] = useState<string | null>(null);
  const [newReviewer, setNewReviewer] = useState('');

  const email = (user.email || '').toLowerCase();
  const isAdmin = user.role === 'SUPER_ADMIN';
  const canManage = isAdmin || user.role === 'PROJECT_MANAGER';

  const signoffs = useQuery({
    queryKey: ['signoffs', projectId, pending.phase],
    queryFn: () => api.get<SignoffStatus>(`/api/projects/${projectId}/phase/${pending.phase}/signoffs`),
    refetchInterval: 8000,
  });
  const so = signoffs.data;
  const reviewers = so?.reviewers ?? [];
  const canSign = isAdmin || reviewers.includes(email);
  const columns = [...reviewers];
  if (canSign && email && !columns.includes(email)) columns.push(email);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['signoffs', projectId, pending.phase] });
    void qc.invalidateQueries({ queryKey: ['project', projectId] });
    void qc.invalidateQueries({ queryKey: ['artefacts', projectId] });
    void qc.invalidateQueries({ queryKey: ['flow', projectId] });
  };

  const assign = useMutation({
    mutationFn: (v: { target: string; user: string; assigned: boolean }) =>
      api.put(`/api/projects/${projectId}/phase/${pending.phase}/assign`, v),
    onSuccess: invalidate,
  });
  const signTarget = useMutation({
    mutationFn: (target: string) =>
      api.post(`/api/projects/${projectId}/phase/${pending.phase}/signoff`, { target }),
    onSuccess: invalidate,
  });
  const setReviewers = useMutation({
    mutationFn: (users: string[]) =>
      api.put(`/api/projects/${projectId}/phase/${pending.phase}/reviewers`, { users }),
    onSuccess: invalidate,
  });
  const review = useMutation({
    mutationFn: (body: { decision: 'APPROVE' | 'AMEND'; comments?: string }) =>
      api.post(`/api/gates/${projectId}/phase/${pending.phase}/review`, body),
    onSuccess: () => { invalidate(); setAmendOpen(false); setComments(''); },
  });

  const phaseArtefacts = artefacts.filter((a) => a.phase === pending.phase);
  const titleFor = (id: string) => phaseArtefacts.find((a) => a.id === id)?.title ?? id;
  const busy = assign.isPending || signTarget.isPending || setReviewers.isPending || review.isPending;
  const short = (e: string) => e.split('@')[0];
  const rows = so?.rows ?? [];

  const addReviewer = () => {
    const e = newReviewer.trim().toLowerCase();
    if (!e || reviewers.includes(e)) { setNewReviewer(''); return; }
    setReviewers.mutate([...reviewers, e]);
    setNewReviewer('');
  };

  const rowLabel = (r: Row) => {
    if (r.kind === 'stage') return <span className="font-semibold text-navy">🗂 Entire stage</span>;
    if (r.kind === 'type') return <span><span className="rounded bg-violet-50 px-1 py-0.5 text-[9px] font-semibold text-violet-700">TYPE</span> {r.type}</span>;
    const reviewed = so?.reviewedArtefacts.includes(r.artefactId!);
    return (
      <span className="inline-flex items-center gap-1.5">
        <span className={reviewed ? 'text-emerald-500' : 'text-slate-300'}>{reviewed ? '✓' : '○'}</span>
        <span className="rounded bg-brand-50 px-1 py-0.5 text-[9px] font-semibold text-brand-700">{r.type}</span>
        <button onClick={() => setViewArtefactId(r.artefactId!)} className="max-w-[200px] truncate text-left text-xs text-brand-700 hover:underline" title={titleFor(r.artefactId!)}>
          {titleFor(r.artefactId!)}
        </button>
      </span>
    );
  };

  const renderCell = (r: Row, u: string) => {
    const c = so?.cells[r.target] ?? { assigned: [], signed: [] };
    const assigned = c.assigned.includes(u);
    const signed = c.signed.includes(u);
    const mine = u === email;
    if (signed) return <span className="text-emerald-600" title={`Signed by ${u}`}>✓</span>;
    if (assigned) {
      return (
        <span className="inline-flex items-center gap-1">
          {mine && canSign ? (
            <button
              onClick={() => signTarget.mutate(r.target)}
              disabled={busy}
              className="rounded bg-emerald-600 px-1.5 py-0.5 text-[10px] font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
              title="Sign off this item"
            >sign</button>
          ) : (
            <span className="rounded bg-amber-100 px-1 py-0.5 text-[9px] font-semibold text-amber-700" title="Assigned, awaiting sign-off">assigned</span>
          )}
          {canManage && (
            <button onClick={() => assign.mutate({ target: r.target, user: u, assigned: false })} disabled={busy}
              className="text-[10px] text-slate-300 hover:text-bared-600" title="Unassign">✕</button>
          )}
        </span>
      );
    }
    // unassigned
    if (canManage) {
      return (
        <button onClick={() => assign.mutate({ target: r.target, user: u, assigned: true })} disabled={busy}
          className="mx-auto block h-4 w-4 rounded border border-dashed border-slate-300 text-[10px] leading-none text-slate-400 hover:border-brand-400 hover:text-brand-600"
          title="Assign this reviewer to review this item">+</button>
      );
    }
    return <span className="text-slate-200">·</span>;
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
            Assign reviewers to the entire stage, an output type, or specific documents. Each reviewer signs off what
            they're assigned; the stage completes once every document is reviewed.
          </div>
        </div>
      </div>

      {/* Review matrix */}
      <div className="mt-3 rounded-lg bg-white p-3">
        <div className="mb-2 flex items-center justify-between">
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">
            Review matrix — documents reviewed ({so?.signedCount ?? 0}/{so?.totalCount ?? 0})
          </div>
          {so?.complete
            ? <span className="text-[11px] font-semibold text-emerald-600">✓ All reviews complete</span>
            : (so && so.totalCount - so.signedCount > 0)
              ? <span className="text-[11px] text-amber-700">{so.totalCount - so.signedCount} document(s) pending</span>
              : null}
        </div>

        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-slate-200">
                <th className="sticky left-0 z-10 bg-white px-2 py-1.5 text-left text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                  Review item
                </th>
                {columns.map((u) => (
                  <th key={u} className="px-2 py-1.5 text-center align-bottom">
                    <div className="flex flex-col items-center gap-0.5">
                      <span className={`max-w-[90px] truncate text-[11px] font-semibold ${u === email ? 'text-brand-700' : 'text-slate-600'}`} title={u}>
                        {short(u)}{u === email ? ' (you)' : ''}
                      </span>
                      {canManage && u !== email && (
                        <button onClick={() => setReviewers.mutate(reviewers.filter((r) => r !== u))} disabled={busy}
                          className="text-[9px] text-slate-300 hover:text-bared-600" title="Remove reviewer">✕ remove</button>
                      )}
                    </div>
                  </th>
                ))}
                {columns.length === 0 && <th className="px-2 py-1.5 text-[11px] font-normal text-slate-400">Add a reviewer below.</th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.target} className={`border-t border-slate-100 ${r.kind !== 'artifact' ? 'bg-slate-50/60' : ''}`}>
                  <td className="sticky left-0 z-10 bg-inherit px-2 py-1.5">{rowLabel(r)}</td>
                  {columns.map((u) => (
                    <td key={u} className="px-2 py-1.5 text-center">{renderCell(r, u)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {canManage && (
          <div className="mt-2 flex items-center gap-2">
            <input
              className="w-64 rounded border border-slate-300 px-2 py-1 text-xs focus:border-brand-500 focus:outline-none"
              placeholder="Add reviewer email…"
              value={newReviewer}
              onChange={(e) => setNewReviewer(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') addReviewer(); }}
            />
            <button onClick={addReviewer} disabled={busy || !newReviewer.trim()}
              className="rounded bg-brand-600 px-3 py-1 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-40">
              + Add reviewer
            </button>
          </div>
        )}
        <div className="mt-1.5 text-[10px] text-slate-400">
          Assigning <span className="font-semibold">Entire stage</span> makes a reviewer responsible for everything;
          assigning a <span className="font-semibold">type</span> covers all its documents. A reviewer can be assigned
          to several items, and each item can have multiple reviewers.
        </div>
        {(assign.isError || signTarget.isError || setReviewers.isError) && (
          <div className="mt-1.5 text-[11px] text-bared-600">
            {(assign.error || signTarget.error || setReviewers.error) instanceof Error
              ? ((assign.error || signTarget.error || setReviewers.error) as Error).message : 'Action failed'}
          </div>
        )}
      </div>

      {viewArtefactId && (
        <ArtifactViewer projectId={projectId} artefactId={viewArtefactId} onClose={() => setViewArtefactId(null)} canRepair={canSign} />
      )}

      {/* Request changes + admin override */}
      <div className="mt-3">
        {(canSign || canManage) ? (
          !amendOpen ? (
            <div className="flex flex-wrap gap-2">
              <button onClick={() => setAmendOpen(true)} disabled={busy}
                className="rounded-lg bg-orange-500 px-4 py-2 text-sm font-semibold text-white hover:bg-orange-600 disabled:opacity-50">
                ↺ Request amendments
              </button>
              {isAdmin && !so?.complete && (
                <button onClick={() => review.mutate({ decision: 'APPROVE' })} disabled={busy}
                  className="rounded-lg border border-red-300 px-4 py-2 text-sm font-semibold text-red-700 hover:bg-red-50 disabled:opacity-50"
                  title="Super-admin override — force-complete the gate without every sign-off (audited).">
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
                <button onClick={() => review.mutate({ decision: 'AMEND', comments })}
                  disabled={review.isPending || comments.trim().length === 0}
                  className="rounded-lg bg-orange-500 px-4 py-2 text-sm font-semibold text-white hover:bg-orange-600 disabled:opacity-50">
                  Send feedback — regenerate
                </button>
                <button onClick={() => setAmendOpen(false)} className="rounded-lg px-3 py-2 text-sm text-slate-600 hover:bg-slate-100">Cancel</button>
              </div>
              <div className="text-[11px] text-amber-700">Requesting changes clears sign-offs — reviewers sign the new version.</div>
            </div>
          )
        ) : (
          <div className="rounded-lg bg-white px-3 py-2 text-xs text-slate-500">
            You are not a reviewer for this stage. Its assigned reviewers must sign off before it advances.
          </div>
        )}
        {review.isError && (
          <div className="mt-2 rounded bg-red-50 px-3 py-2 text-xs text-red-700">
            {review.error instanceof Error ? review.error.message : 'Action failed'}
          </div>
        )}
      </div>
    </div>
  );
}
