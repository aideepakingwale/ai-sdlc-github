import { useState, type CSSProperties, type ReactNode } from 'react';
import Splitter from './Splitter';

const read = (key: string, def: number, min: number, max: number): number => {
  try { const n = Number(localStorage.getItem(key)); return Number.isFinite(n) && n >= min && n <= max ? n : def; } catch { return def; }
};

/**
 * Two panes side by side with a draggable divider (stacked on narrow screens). The left width is remembered under
 * `storageKey`; double-click the divider to reset it.
 */
export default function SplitPair({ storageKey, left, right, def = 280, min = 160, max = 640, label = 'Resize the panes', className = '' }: {
  storageKey: string; left: ReactNode; right: ReactNode; def?: number; min?: number; max?: number; label?: string; className?: string;
}) {
  const [w, setW] = useState(() => read(storageKey, def, min, max));
  const set = (n: number) => { setW(n); try { localStorage.setItem(storageKey, String(n)); } catch { /* ignore */ } };
  return (
    <div className={`flex flex-col gap-3 md:flex-row md:gap-0 ${className}`} data-testid="v2-splitpair">
      <div className="min-w-0 md:w-[var(--split-w)] md:shrink-0" style={{ '--split-w': `${w}px` } as CSSProperties} data-testid="v2-split-left">{left}</div>
      <div className="hidden md:flex"><Splitter value={w} min={min} max={max} edge="right" onChange={set} onReset={() => set(def)} label={label} testid="v2-split-inner" /></div>
      <div className="min-w-0 flex-1 md:pl-2">{right}</div>
    </div>
  );
}
