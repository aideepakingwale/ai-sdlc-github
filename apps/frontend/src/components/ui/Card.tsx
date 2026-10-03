import { useState, type ReactNode } from 'react';
import { Icon, type IconName } from './Icon';

/** A titled card with an optional step number, a plain-language subtitle and (optionally) collapsible body. */
export function Card({
  title, subtitle, icon, step, status, actions, children, collapsible = false, defaultOpen = true, className = '', tone,
}: {
  title: ReactNode; subtitle?: ReactNode; icon?: IconName; step?: number; status?: ReactNode; actions?: ReactNode;
  children?: ReactNode; collapsible?: boolean; defaultOpen?: boolean; className?: string; tone?: 'default' | 'blue' | 'amber' | 'red' | 'green';
}) {
  const [open, setOpen] = useState(defaultOpen);
  const ring = { default: 'border-slate-200', blue: 'border-blue-200', amber: 'border-amber-300', red: 'border-red-200', green: 'border-emerald-200' }[tone ?? 'default'];
  return (
    <section className={`rounded-xl border bg-white shadow-sm ${ring} ${className}`}>
      <header className="flex items-start gap-3 px-4 py-3">
        {step != null ? (
          <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-600 text-xs font-bold text-white">{step}</span>
        ) : icon ? (
          <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-slate-100 text-slate-600"><Icon name={icon} size={14} /></span>
        ) : null}
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-slate-800">{title}</h3>
          {subtitle && <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p>}
        </div>
        {status}
        {actions}
        {collapsible && (
          <button
            type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
            className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
            title={open ? 'Collapse' : 'Expand'}
          >
            <Icon name={open ? 'chevron-down' : 'chevron-right'} size={16} label={open ? 'Collapse' : 'Expand'} />
          </button>
        )}
      </header>
      {(!collapsible || open) && children != null && <div className="border-t border-slate-100 px-4 py-3">{children}</div>}
    </section>
  );
}

/** Small section heading used INSIDE cards: readable (not a 10px grey caps label), with an icon and help text. */
export function SectionLabel({ icon, children, hint }: { icon?: IconName; children: ReactNode; hint?: ReactNode }) {
  return (
    <div className="mb-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5">
      {icon && <Icon name={icon} size={14} className="text-slate-400" />}
      <span className="text-xs font-semibold text-slate-700">{children}</span>
      {hint && <span className="text-[11px] text-slate-400">{hint}</span>}
    </div>
  );
}
