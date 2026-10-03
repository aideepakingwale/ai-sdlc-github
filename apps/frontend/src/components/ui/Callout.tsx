import type { ReactNode } from 'react';
import { Icon, type IconName } from './Icon';

/**
 * ONE colour language everywhere in the app:
 *   red    = error / blocked        — something failed or cannot proceed
 *   yellow = advice / attention     — a warning, a recommendation, or a decision for you
 *   green  = success / done / safe
 *   blue   = information / in progress
 *   grey   = neutral / not started
 * Using a Callout (instead of ad-hoc coloured boxes) keeps that meaning consistent.
 */
export type Tone = 'error' | 'warning' | 'advice' | 'info' | 'success' | 'neutral';

export const TONE: Record<Tone, { box: string; icon: string; title: string; iconName: IconName; label: string }> = {
  error: { box: 'border-red-200 bg-red-50', icon: 'text-red-600', title: 'text-red-800', iconName: 'error', label: 'Error' },
  warning: { box: 'border-amber-300 bg-amber-50', icon: 'text-amber-600', title: 'text-amber-900', iconName: 'warning', label: 'Warning' },
  advice: { box: 'border-amber-300 bg-amber-50', icon: 'text-amber-600', title: 'text-amber-900', iconName: 'advice', label: 'Advice' },
  info: { box: 'border-blue-200 bg-blue-50', icon: 'text-blue-600', title: 'text-blue-900', iconName: 'info', label: 'Info' },
  success: { box: 'border-emerald-200 bg-emerald-50', icon: 'text-emerald-600', title: 'text-emerald-900', iconName: 'success', label: 'Done' },
  neutral: { box: 'border-slate-200 bg-slate-50', icon: 'text-slate-500', title: 'text-slate-800', iconName: 'info', label: 'Note' },
};

export function Callout({
  tone = 'info', title, children, action, icon, className = '', compact = false, spin = false,
}: {
  tone?: Tone; title?: ReactNode; children?: ReactNode; action?: ReactNode; icon?: IconName;
  className?: string; compact?: boolean; spin?: boolean;
}) {
  const t = TONE[tone];
  return (
    <div
      role={tone === 'error' || tone === 'warning' ? 'alert' : 'status'}
      data-tone={tone}
      className={`flex items-start gap-3 rounded-lg border ${compact ? 'px-3 py-2' : 'px-4 py-3'} ${t.box} ${className}`}
    >
      <Icon name={icon ?? t.iconName} size={compact ? 16 : 18} className={`mt-0.5 ${t.icon}`} spin={spin} />
      <div className="min-w-0 flex-1 text-[13px] leading-snug text-slate-700">
        {title && <div className={`font-semibold ${t.title}`}>{title}</div>}
        {children && <div className={title ? 'mt-0.5' : ''}>{children}</div>}
      </div>
      {action && <div className="shrink-0 self-center">{action}</div>}
    </div>
  );
}
