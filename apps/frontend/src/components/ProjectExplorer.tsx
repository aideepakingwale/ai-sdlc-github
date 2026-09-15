import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { COLOR_CLASSES, type ProjectFlow } from '../api/flow';
import type { Project } from '../api/types';

interface Props {
  onOpen: (projectId: string) => void;
  onClose: () => void;
}

/**
 * Project Explorer: Admin / PM oversight across all their projects —
 * every project's stage states, assignees and progress at a glance, with a
 * click-through into the workspace. Backed by the same RBAC-scoped
 * /api/projects list, so a PM sees their projects and SUPER_ADMIN sees all.
 */
export default function ProjectExplorer({ onOpen, onClose }: Props) {
  const projects = useQuery({
    queryKey: ['projects'],
    queryFn: () => api.get<{ projects: Project[] }>('/api/projects'),
  });

  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center bg-black/40 p-6" onClick={onClose}>
      <div
        className="flex max-h-[88vh] w-full max-w-5xl flex-col rounded-2xl bg-slate-50 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-200 bg-white px-5 py-3">
          <div>
            <div className="text-sm font-bold text-slate-800">Project Explorer</div>
            <div className="text-xs text-slate-500">
              All projects you manage — stage state, assignees and progress
            </div>
          </div>
          <button onClick={onClose} className="rounded px-2 py-1 text-slate-400 hover:bg-slate-100">
            ✕
          </button>
        </div>
        <div className="grid grid-cols-1 gap-3 overflow-y-auto p-4 md:grid-cols-2">
          {(projects.data?.projects ?? []).map((p) => (
            <ExplorerCard key={p.id} project={p} onOpen={onOpen} />
          ))}
          {(projects.data?.projects ?? []).length === 0 && (
            <div className="col-span-full rounded-lg bg-white p-6 text-center text-sm text-slate-500">
              No projects yet.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ExplorerCard({ project, onOpen }: { project: Project; onOpen: (id: string) => void }) {
  const flow = useQuery({
    queryKey: ['flow', project.id],
    queryFn: () => api.get<ProjectFlow>(`/api/projects/${project.id}/flow`),
  });

  return (
    <button
      onClick={() => onOpen(project.id)}
      className="rounded-xl border border-slate-200 bg-white p-3 text-left transition hover:border-brand-300 hover:shadow-md"
    >
      <div className="flex items-center justify-between">
        <div className="truncate text-sm font-semibold text-slate-800">{project.name}</div>
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold text-slate-500">
          {project.status}
        </span>
      </div>
      <div className="mt-0.5 text-[11px] text-slate-400">
        Phase {project.currentPhase}/6 · {project.techStack ?? ''}
      </div>
      <div className="mt-2 flex gap-1">
        {(flow.data?.stages ?? []).map((s) => (
          <div
            key={s.phase}
            className={`h-1.5 flex-1 rounded-full ${COLOR_CLASSES[s.color].dot}`}
            title={`${s.reviewerRole}: ${COLOR_CLASSES[s.color].label}${s.assignee ? ` · ${s.assignee.displayName}` : ''}`}
          />
        ))}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-1">
        {(flow.data?.stages ?? [])
          .filter((s) => s.assignee)
          .map((s) => (
            <span key={s.phase} className="rounded bg-slate-100 px-1.5 py-0.5 text-[9px] text-slate-600">
              {s.reviewerRole}: {s.assignee?.displayName.split(' ')[0]}
            </span>
          ))}
      </div>
    </button>
  );
}
