import type { ReactNode } from 'react';
import { COLOR_CLASSES, type StageColor } from '../api/flow';
import { Icon } from '../components/ui/Icon';

/** The round status marker used for a stage in the sidebar, the pipeline view and the mini-map. */
export function StageDot({ n, color, size = 22 }: { n: number | string; color: StageColor; size?: number }) {
  const done = color === 'emerald';
  const ring = { slate: 'border-slate-300 text-slate-500 bg-white', blue: 'border-brand-500 text-brand-600 bg-white', amber: 'border-amber-500 text-amber-700 bg-amber-50', orange: 'border-orange-500 text-orange-700 bg-orange-50', emerald: 'border-emerald-600 bg-emerald-600 text-white', red: 'border-bared-500 text-bared-600 bg-bared-200' }[color];
  return (
    <span
      className={`inline-flex shrink-0 items-center justify-center rounded-full border-2 text-[11px] font-semibold ${ring} ${color === 'blue' ? 'animate-pulse' : ''}`}
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      {done ? <Icon name="check" size={size - 10} strokeWidth={3} /> : n}
    </span>
  );
}

export function statusChipClass(color: StageColor): string {
  return COLOR_CLASSES[color].chip;
}

/** A small pill, used for status and facts in headers. */
export function Pill({ children, tone = 'slate', title }: { children: ReactNode; tone?: 'slate' | 'brand' | 'green' | 'amber' | 'red' | 'orange'; title?: string }) {
  const cls = { slate: 'bg-slate-100 text-slate-600', brand: 'bg-brand-100 text-brand-700', green: 'bg-emerald-100 text-emerald-700', amber: 'bg-amber-100 text-amber-700', red: 'bg-bared-200 text-bared-700', orange: 'bg-orange-100 text-orange-700' }[tone];
  return <span title={title} className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium ${cls}`}>{children}</span>;
}

export const stageTone = (c: StageColor): 'slate' | 'brand' | 'green' | 'amber' | 'red' | 'orange' => ({ slate: 'slate', blue: 'brand', amber: 'amber', orange: 'orange', emerald: 'green', red: 'red' } as const)[c];

export function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]!.toUpperCase()).join('') || '?';
}
