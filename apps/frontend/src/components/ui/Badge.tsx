import type { ReactNode } from 'react';
import { Icon, type IconName } from './Icon';

export const STATUS_BADGE: Record<string, { label: string; cls: string; icon: IconName; spin?: boolean; hint: string }> = {
  NOT_STARTED: { label: 'Not started', cls: 'bg-slate-100 text-slate-600 ring-slate-200', icon: 'circle', hint: 'Nothing has been generated for this stage yet.' },
  IN_PROGRESS: { label: 'Generating', cls: 'bg-blue-100 text-blue-700 ring-blue-200', icon: 'loader', spin: true, hint: 'The agent is generating this stage. It keeps running if you leave.' },
  PENDING_REVIEW: { label: 'Awaiting review', cls: 'bg-amber-100 text-amber-800 ring-amber-200', icon: 'eye', hint: 'Generated — a reviewer must approve or request changes.' },
  APPROVED: { label: 'Approved', cls: 'bg-emerald-100 text-emerald-700 ring-emerald-200', icon: 'success', hint: 'Signed off. The next stage can start.' },
  AMEND_REQUESTED: { label: 'Changes requested', cls: 'bg-orange-100 text-orange-800 ring-orange-200', icon: 'edit', hint: 'A reviewer asked for changes; update the plan and regenerate.' },
  ESCALATED: { label: 'Escalated', cls: 'bg-red-100 text-red-700 ring-red-200', icon: 'error', hint: 'Automation gave up — a human needs to step in.' },
};

export function StatusBadge({ status, className = '' }: { status: string; className?: string }) {
  const s = STATUS_BADGE[status] ?? STATUS_BADGE.NOT_STARTED!;
  return (
    <span
      title={s.hint}
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset ${s.cls} ${className}`}
    >
      <Icon name={s.icon} size={12} spin={s.spin} />
      {s.label}
    </span>
  );
}

const TONES: Record<string, string> = {
  neutral: 'bg-slate-100 text-slate-600 ring-slate-200',
  info: 'bg-blue-50 text-blue-700 ring-blue-200',
  success: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  warning: 'bg-amber-50 text-amber-800 ring-amber-200',
  error: 'bg-red-50 text-red-700 ring-red-200',
  brand: 'bg-brand-50 text-brand-700 ring-brand-200',
};

export function Badge({ tone = 'neutral', icon, children, title }: {
  tone?: keyof typeof TONES; icon?: IconName; children: ReactNode; title?: string;
}) {
  return (
    <span title={title} className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${TONES[tone]}`}>
      {icon && <Icon name={icon} size={11} />}
      {children}
    </span>
  );
}
