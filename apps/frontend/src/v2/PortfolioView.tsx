import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import type { StageColor } from '../api/flow';
import type { Project } from '../api/types';

export interface PortfolioProject extends Project {
  stages: Array<{ phase: number; name: string; color: StageColor; status: string }>;
  approved: number; awaitingReview: boolean; escalated: boolean; lastActivity: string | null;
}

const DOT: Record<StageColor, string> = {
  slate: 'bg-slate-300', blue: 'bg-brand-500', amber: 'bg-amber-500', orange: 'bg-orange-500', emerald: 'bg-emerald-600', red: 'bg-bared-500',
};

export function portfolioStatus(p: Pick<PortfolioProject, 'stages' | 'approved' | 'awaitingReview' | 'escalated'>): string {
  if (p.escalated) return 'Escalated';
  if (p.awaitingReview) return 'Awaiting review';
  if (p.stages.length > 0 && p.approved === p.stages.length) return 'Completed';
  if (p.stages.some((s) => s.status !== 'NOT_STARTED')) return 'In progress';
  return 'Not started';
}

export function portfolioTotals(list: PortfolioProject[]) {
  return {
    projects: list.length, awaiting: list.filter((p) => p.awaitingReview).length,
    escalated: list.filter((p) => p.escalated).length, approved: list.reduce((n, p) => n + p.approved, 0),
  };
}

function ago(iso: string | null): string {
  if (!iso) return '';
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (!Number.isFinite(s)) return '';
  if (s < 60) return 'just now';
  const m = Math.floor(s / 60); if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60); if (h < 24) return `${h} h ago`;
  const d = Math.floor(h / 24); return d === 1 ? 'yesterday' : d < 30 ? `${d} days ago` : new Date(iso).toLocaleDateString();
}

/** All projects as a table: stage dots, progress, stack and last activity, as the design shows. */
export default function PortfolioView({ onOpen, onNewProject, canManage }: { onOpen: (id: string) => void; onNewProject: () => void; canManage: boolean }) {
  const q = useQuery({
    queryKey: ['portfolio'],
    queryFn: () => api.get<{ projects: PortfolioProject[] }>('/api/projects/portfolio'),
    refetchInterval: 20_000,
  });
  const list = q.data?.projects ?? [];
  const t = portfolioTotals(list);
  const tiles: Array<[number, string, string]> = [
    [t.projects, 'Projects', 'text-navy'], [t.awaiting, 'Awaiting review', 'text-amber-600'],
    [t.escalated, 'Escalated', 'text-bared-600'], [t.approved, 'Stages approved', 'text-emerald-700'],
  ];
  return (
    <div className="h-full overflow-y-auto" data-testid="v2-portfolio">
      <div className="mx-auto max-w-6xl px-6 py-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="font-display text-2xl font-bold text-navy">All projects</h1>
            <p className="mt-0.5 text-sm text-slate-500">Status of every project you can see.</p>
          </div>
          {canManage && <button type="button" onClick={onNewProject} data-testid="v2-portfolio-new" className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700">+ New project</button>}
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">
          {tiles.map(([n, label, tone]) => (
            <div key={label} className="rounded-xl border border-slate-200 bg-white p-4">
              <div className={`font-display text-3xl font-bold ${tone}`}>{n}</div>
              <div className="mt-0.5 text-xs text-slate-500">{label}</div>
            </div>
          ))}
        </div>
        {q.isLoading && <div className="mt-6 animate-pulse text-sm text-slate-400">Loading…</div>}
        {!q.isLoading && list.length === 0 && <div className="mt-6 rounded-xl bg-slate-100 p-6 text-center text-sm text-slate-500">No projects yet.</div>}
        {list.length > 0 && (
          <div className="mt-4 overflow-x-auto rounded-xl border border-slate-200 bg-white">
            <table className="w-full min-w-[760px] text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs font-normal text-slate-500">
                  {['Project', 'Stages', 'Progress', 'Stack', 'Last activity', ''].map((h) => <th key={h} className="px-4 py-2.5 font-normal">{h}</th>)}
                </tr>
              </thead>
              <tbody>
                {list.map((p) => (
                  <tr key={p.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50" data-testid="v2-portfolio-row">
                    <td className="px-4 py-3"><div className="font-semibold text-navy">{p.name}</div><div className="text-xs text-slate-500">{portfolioStatus(p)}</div></td>
                    <td className="px-4 py-3"><span className="flex gap-1.5" aria-label={`${p.approved} of ${p.stages.length} stages approved`}>
                      {p.stages.map((s) => <i key={s.phase} title={s.name} className={`h-2.5 w-2.5 rounded-full ${DOT[s.color]}`} />)}</span></td>
                    <td className="px-4 py-3">
                      <div className="h-1.5 w-28 overflow-hidden rounded-full bg-slate-200"><div className="h-full bg-brand-600" style={{ width: `${p.stages.length ? (p.approved / p.stages.length) * 100 : 0}%` }} /></div>
                      <div className="mt-0.5 text-xs text-slate-500">{p.approved} of {p.stages.length}</div>
                    </td>
                    <td className="px-4 py-3 text-slate-700">{p.techStack || <span className="text-slate-400">Not decided</span>}</td>
                    <td className="px-4 py-3 text-slate-700">{ago(p.lastActivity ?? p.createdAt)}</td>
                    <td className="px-4 py-3 text-right"><button type="button" onClick={() => onOpen(p.id)} className="rounded-lg border border-slate-300 px-3 py-1 text-xs font-semibold text-slate-700 hover:border-brand-400">Open</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
