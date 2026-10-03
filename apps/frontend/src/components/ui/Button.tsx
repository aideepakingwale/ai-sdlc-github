import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { Icon, type IconName } from './Icon';

/**
 * Button hierarchy: ONE primary action per card (the thing to do next), secondary for
 * alternatives, ghost for low-emphasis, danger for destructive. Disabled buttons should always
 * be paired with a visible reason (see `reason`), never just greyed out.
 */
type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'warning';

const VARIANT: Record<Variant, string> = {
  primary: 'bg-brand-600 text-white hover:bg-brand-700 shadow-sm',
  secondary: 'border border-slate-300 bg-white text-slate-700 hover:border-brand-400 hover:text-brand-700',
  ghost: 'text-slate-600 hover:bg-slate-100',
  danger: 'bg-red-600 text-white hover:bg-red-700 shadow-sm',
  warning: 'border border-amber-400 bg-white text-amber-900 hover:bg-amber-100',
};

export function Button({
  variant = 'secondary', size = 'md', icon, loading = false, children, className = '', disabled, ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: 'sm' | 'md'; icon?: IconName; loading?: boolean; children?: ReactNode }) {
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || loading}
      className={`inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-lg font-semibold transition disabled:cursor-not-allowed disabled:opacity-45 ${
        size === 'sm' ? 'px-2.5 py-1 text-xs' : 'px-4 py-2 text-sm'} ${VARIANT[variant]} ${className}`}
    >
      {loading ? <Icon name="loader" size={size === 'sm' ? 13 : 15} spin /> : icon ? <Icon name={icon} size={size === 'sm' ? 13 : 15} /> : null}
      {children}
    </button>
  );
}
