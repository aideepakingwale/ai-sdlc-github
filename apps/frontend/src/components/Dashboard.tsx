import type { Project } from '../api/types';

/** Coarse health/status bucket for a project, from its status string. */
function statusMeta(status: string): { label: string; dot: string; chip: string } {
  const s = status.toUpperCase();
  if (s.includes('COMPLETE') || s === 'DONE') return { label: 'Completed', dot: 'bg-emerald-500', chip: 'bg-emerald-50 text-emerald-700' };
  if (s.includes('REVIEW') || s.includes('PENDING')) return { label: 'In review', dot: 'bg-amber-500', chip: 'bg-amber-50 text-amber-700' };
  if (s.includes('ESCALAT') || s.includes('BLOCK')) return { label: 'Needs attention', dot: 'bg-bared-500', chip: 'bg-bared-200 text-bared-700' };
  if (s.includes('ACTIVE') || s.includes('PROGRESS') || s.includes('RUNNING')) return { label: 'In progress', dot: 'bg-brand-500', chip: 'bg-brand-50 text-brand-700' };
  return { label: status || 'Draft', dot: 'bg-slate-400', chip: 'bg-slate-100 text-slate-600' };
}

function timeAgo(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const s = Math.floor((Date.now() - then) / 1000);
  if (s < 60) return 'just now';
  const m = Math.floor(s / 60); if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60); if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24); if (d < 30) return `${d}d ago`;
  return new Date(iso).toLocaleDateString();
}

/**
 * Portfolio landing: an at-a-glance view of every project (status, phase, tech,
 * last activity) with headline counts — replaces dropping the user straight into
 * a single empty workspace.
 */
export default function Dashboard({
  projects,
  onOpen,
  onNewProject,
  canManage,
  loading,
}: {
  projects: Project[];
  onOpen: (id: string) => void;
  onNewProject: () => void;
  canManage: boolean;
  loading: boolean;
}) {
  const total = projects.length;
  const inReview = projects.filter((p) => /review|pending/i.test(p.status)).length;
  const attention = projects.filter((p) => /escalat|block/i.test(p.status)).length;
  const active = projects.filter((p) => /active|progress|running/i.test(p.status)).length;

  const kpis = [
    { label: 'Projects', value: total, tone: 'text-navy' },
    { label: 'In progress', value: active, tone: 'text-brand-600' },
    { label: 'Awaiting review', value: inReview, tone: 'text-amber-600' },
    { label: 'Needs attention', value: attention, tone: 'text-bared-600' },
  ];

  return (
    <div className="mx-auto h-full w-full max-w-6xl overflow-y-auto px-6 py-8">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-navy">Project portfolio</h1>
          <p className="mt-1 text-sm text-slate-500">Every SDLC pipeline you can access, with live status and health.</p>
        </div>
        {canManage && (
          <button
            onClick={onNewProject}
            className="rounded-md bg-brand-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-brand-700"
          >
            + New project
          </button>
        )}
      </div>

      {/* KPI row */}
      <div className="mt-6 grid grid-cols-2 gap-3 md:grid-cols-4">
        {kpis.map((k) => (
          <div key={k.label} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className={`text-3xl font-semibold ${k.tone}`}>{k.value}</div>
            <div className="mt-1 text-xs font-medium uppercase tracking-wide text-slate-500">{k.label}</div>
          </div>
        ))}
      </div>

      {/* Project grid */}
      <div className="mt-8">
        {loading ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="h-32 animate-pulse rounded-xl border border-slate-200 bg-white" />
            ))}
          </div>
        ) : total === 0 ? (
          <div className="rounded-xl border border-dashed border-slate-300 bg-white p-12 text-center">
            <div className="text-4xl">🗂</div>
            <div className="mt-3 text-lg font-semibold text-navy">No projects yet</div>
            <p className="mx-auto mt-1 max-w-md text-sm text-slate-500">
              {canManage
                ? 'Create your first project to launch an end-to-end, human-in-the-loop SDLC pipeline.'
                : 'A Project Manager will create a project and add you to its team.'}
            </p>
            {canManage && (
              <button onClick={onNewProject} className="mt-4 rounded-md bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700">
                + New project
              </button>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {projects.map((p) => {
              const sm = statusMeta(p.status);
              return (
                <button
                  key={p.id}
                  onClick={() => onOpen(p.id)}
                  className="group flex flex-col rounded-xl border border-slate-200 bg-white p-4 text-left shadow-sm transition hover:border-brand-300 hover:shadow-md"
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="min-w-0 truncate text-sm font-semibold text-navy group-hover:text-brand-700">{p.name}</span>
                    <span className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${sm.chip}`}>
                      <span className={`h-1.5 w-1.5 rounded-full ${sm.dot}`} /> {sm.label}
                    </span>
                  </div>
                  <div className="mt-3 flex items-center gap-2 text-xs text-slate-500">
                    <span className="rounded bg-slate-100 px-1.5 py-0.5 font-medium text-slate-600">Phase {p.currentPhase}</span>
                    {p.techStack && <span className="truncate" title={p.techStack}>{p.techStack}</span>}
                  </div>
                  <div className="mt-3 flex items-center justify-between border-t border-slate-100 pt-2 text-[11px] text-slate-400">
                    <span>Updated {timeAgo(p.createdAt)}</span>
                    <span className="text-brand-600 opacity-0 transition group-hover:opacity-100">Open →</span>
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
