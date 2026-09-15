import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';

interface PhaseBundle {
  phase: number;
  stage: {
    name: string;
    persona: string;
    reviewerRole: string;
    status: string;
    assignee: { displayName: string; email: string } | null;
    reviewedBy: string | null;
    canReview: boolean;
    canRetrigger: boolean;
  };
  artefacts: Array<{ id: string; type: string; title: string; url: string | null; createdAt: string }>;
  tasks: Array<{
    id: string;
    event: string;
    agentRole: string;
    humanReviewer: string | null;
    provider: string | null;
    model: string | null;
    timestamp: string;
  }>;
}

const EVENT_ICON: Record<string, string> = {
  'ai.generation': '🤖',
  'skill.executed': '⚡',
  'gate.approved': '✅',
  'gate.approved_override': '⚡✅',
  'gate.amend_requested': '↺',
  'gate.pending_review': '⏸',
  'stage.retriggered': '↻',
  'build.succeeded': '🟢',
  'build.escalated': '🚨',
  'project.created': '📁',
};

/**
 * Phase-scoped view: the tasks (activity) + artifacts of one stage,
 * for any user authorised on the project. Opens when a pipeline stage is
 * clicked.
 */
export default function PhasePanel({ projectId, phase, onOpenArtifact }: { projectId: string; phase: number; onOpenArtifact: (id: string) => void }) {
  const bundle = useQuery({
    queryKey: ['phase', projectId, phase],
    queryFn: () => api.get<PhaseBundle>(`/api/projects/${projectId}/phases/${phase}`),
    refetchInterval: 5_000,
  });

  if (!bundle.data) return <div className="animate-pulse p-3 text-sm text-slate-400">Loading phase…</div>;
  const { stage, artefacts, tasks } = bundle.data;

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-brand-200 bg-brand-50 p-2.5">
        <div className="text-sm font-semibold text-brand-800">
          Phase {phase}: {stage.name}
        </div>
        <div className="mt-0.5 text-[11px] text-slate-500">
          {stage.persona} · gate: {stage.reviewerRole} · <b>{stage.status.replace('_', ' ').toLowerCase()}</b>
        </div>
        <div className="mt-0.5 text-[11px] text-slate-500">
          Assigned: {stage.assignee ? `${stage.assignee.displayName}` : 'unassigned'}
          {stage.reviewedBy ? ` · reviewed by ${stage.reviewedBy.split('@')[0]}` : ''}
        </div>
      </div>

      <div>
        <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">
          Artifacts ({artefacts.length})
        </div>
        <div className="space-y-1.5">
          {artefacts.length === 0 && <div className="rounded bg-slate-100 p-2 text-center text-xs text-slate-400">No artifacts in this phase yet.</div>}
          {artefacts.map((a) => (
            <button
              key={a.id}
              onClick={() => onOpenArtifact(a.id)}
              className="block w-full rounded-lg border border-slate-200 bg-white p-2 text-left transition hover:border-brand-300 hover:shadow-sm"
            >
              <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold text-slate-600">{a.type}</span>
              <div className="mt-0.5 truncate text-sm font-medium text-slate-800">{a.title}</div>
            </button>
          ))}
        </div>
      </div>

      <div>
        <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">
          Tasks &amp; activity ({tasks.length})
        </div>
        <div className="space-y-1">
          {tasks.length === 0 && <div className="rounded bg-slate-100 p-2 text-center text-xs text-slate-400">No activity in this phase yet.</div>}
          {tasks.map((t) => (
            <div key={t.id} className="flex items-start gap-1.5 rounded bg-white px-2 py-1 text-[11px]">
              <span>{EVENT_ICON[t.event] ?? '•'}</span>
              <div className="min-w-0 flex-1">
                <div className="font-medium text-slate-700">{t.event}</div>
                <div className="truncate text-slate-400">
                  {t.agentRole}
                  {t.provider ? ` · ${t.provider}/${t.model}` : ''}
                  {t.humanReviewer ? ` · 👤 ${t.humanReviewer.split('@')[0]}` : ''}
                </div>
              </div>
              <span className="shrink-0 text-slate-300">{new Date(t.timestamp).toLocaleTimeString()}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
