import { Icon } from './Icon';
import type { StepInfo } from '../../lib/stageGuide';

const STATE_STYLE: Record<StepInfo['state'], { dot: string; text: string; line: string }> = {
  done: { dot: 'bg-emerald-500 text-white', text: 'text-emerald-700', line: 'bg-emerald-300' },
  current: { dot: 'bg-brand-600 text-white ring-4 ring-brand-100', text: 'text-brand-700 font-semibold', line: 'bg-slate-200' },
  attention: { dot: 'bg-amber-500 text-white ring-4 ring-amber-100', text: 'text-amber-800 font-semibold', line: 'bg-slate-200' },
  error: { dot: 'bg-red-600 text-white ring-4 ring-red-100', text: 'text-red-700 font-semibold', line: 'bg-slate-200' },
  todo: { dot: 'bg-slate-200 text-slate-500', text: 'text-slate-400', line: 'bg-slate-200' },
};

/** The four-step journey of every stage — always shows where you are and what comes next. */
export function Stepper({ steps }: { steps: StepInfo[] }) {
  return (
    <ol className="flex items-start" aria-label="Stage progress">
      {steps.map((s, i) => {
        const st = STATE_STYLE[s.state];
        return (
          <li key={s.id} className="flex min-w-0 flex-1 items-start" aria-current={s.state === 'current' || s.state === 'attention' ? 'step' : undefined}>
            <div className="flex min-w-0 flex-col items-center text-center">
              <span className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold ${st.dot}`}>
                {s.state === 'done' ? <Icon name="check" size={14} strokeWidth={3} />
                  : s.state === 'error' ? <Icon name="x" size={14} strokeWidth={3} /> : i + 1}
              </span>
              <span className={`mt-1 text-xs ${st.text}`}>{s.label}</span>
              <span className="hidden text-[11px] leading-tight text-slate-400 sm:block">{s.hint}</span>
            </div>
            {i < steps.length - 1 && <span className={`mx-2 mt-3.5 h-0.5 flex-1 rounded ${st.line}`} aria-hidden />}
          </li>
        );
      })}
    </ol>
  );
}
