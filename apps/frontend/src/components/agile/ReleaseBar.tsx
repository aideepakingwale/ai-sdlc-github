import { useState } from 'react';
import type { AgileOverview, Release } from '../../api/agile';
import { releaseTree } from '../../lib/agileView';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Icon } from '../ui/Icon';

const DOT: Record<Release['status'], string> = { open: 'bg-blue-500', hardening: 'bg-amber-500', closed: 'bg-emerald-500' };

/** The releases of the project, as a lineage. Several can be live at once; pick the one to work in. */
export default function ReleaseBar({ overview, focus, onFocus, onNew }: {
  overview: AgileOverview; focus: string | null; onFocus: (id: string) => void; onNew: () => void;
}) {
  const [showClosed, setShowClosed] = useState(false);
  const all = overview.releases ?? [];
  const tree = releaseTree(all);
  const closed = tree.filter((n) => n.release.status === 'closed');
  const shown = tree.filter((n) => n.release.status !== 'closed' || showClosed || n.release.id === focus);
  const current = overview.currentRelease?.id ?? null;
  return (
    <nav aria-label="Releases" className="flex flex-wrap items-center gap-1.5 border-b border-slate-200 bg-slate-50 px-4 py-2">
      <Icon name="gitbranch" size={14} className="text-slate-400" />
      {shown.map(({ release: r, depth }) => {
        const selected = (focus ?? current) === r.id;
        return (
          <button key={r.id} type="button" onClick={() => onFocus(r.id)} aria-pressed={selected}
            title={r.forkedFrom ? `${r.name} — forked from ${r.forkedFrom}${r.forkBaseline?.sprint ? ` at ${r.forkBaseline.sprint}` : ''}` : r.name}
            className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold transition ${
              selected ? 'border-brand-500 bg-white text-brand-700 shadow-sm' : 'border-slate-200 bg-white/60 text-slate-600 hover:border-slate-300'}`}>
            {depth > 0 && <Icon name="arrow-right" size={11} className="text-slate-400" />}
            <span className={`h-2 w-2 rounded-full ${DOT[r.status]}`} aria-hidden />
            {r.code}
            <span className="max-w-[10rem] truncate font-normal text-slate-500">{r.name}</span>
            {r.setupComplete === false && <Badge tone="warning" title="Starting this release stopped part-way">incomplete</Badge>}
            {r.openIterationId && <Badge tone="info">sprint</Badge>}
          </button>);
      })}
      {closed.length > 0 && !showClosed && (
        <button type="button" onClick={() => setShowClosed(true)} className="text-xs text-slate-500 underline-offset-2 hover:underline">{closed.length} closed</button>)}
      {overview.permissions.canRun && <Button size="sm" className="ml-auto" icon="plus" onClick={onNew}>New release</Button>}
    </nav>
  );
}
