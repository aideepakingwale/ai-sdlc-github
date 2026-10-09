import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import Splitter from './Splitter';

const readManual = (key: string, min: number, max: number): number | null => {
  try {
    const raw = localStorage.getItem(key);
    if (raw == null) return null;
    const n = Number(raw);
    return Number.isFinite(n) && n >= min && n <= max ? n : null;
  } catch { return null; }
};

/** The left width until someone drags the divider: a share of the container, never below `def`. */
export function autoWidth(container: number, def: number, min: number, max: number, share = 0.38): number {
  return Math.max(min, Math.min(max, Math.max(def, Math.round(container * share))));
}

/**
 * Two panes side by side with a draggable divider (stacked on narrow screens). Until the divider is dragged the left pane takes about
 * a third of the width, so a wider window shows more of the left side (long names and paths reveal themselves); a dragged width is
 * remembered under `storageKey`, and double-clicking the divider goes back to automatic.
 */
export default function SplitPair({ storageKey, left, right, def = 280, min = 160, max = 900, label = 'Resize the panes', className = '' }: {
  storageKey: string; left: ReactNode; right: ReactNode; def?: number; min?: number; max?: number; label?: string; className?: string;
}) {
  const root = useRef<HTMLDivElement>(null);
  const [manual, setManual] = useState<number | null>(() => readManual(storageKey, min, max));
  const [auto, setAuto] = useState(def);
  useEffect(() => {
    const el = root.current;
    if (!el) return;
    const run = () => setAuto(autoWidth(el.clientWidth, def, min, max));
    run();
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(run) : null;
    ro?.observe(el);
    return () => ro?.disconnect();
  }, [def, min, max]);
  const w = manual ?? auto;
  const set = (n: number) => { setManual(n); try { localStorage.setItem(storageKey, String(n)); } catch { /* ignore */ } };
  const reset = () => { setManual(null); try { localStorage.removeItem(storageKey); } catch { /* ignore */ } };
  return (
    <div ref={root} className={`flex flex-col gap-3 md:flex-row md:gap-0 ${className}`} data-testid="v2-splitpair">
      <div className="min-w-0 md:w-[var(--split-w)] md:shrink-0" style={{ '--split-w': `${w}px` } as CSSProperties} data-testid="v2-split-left">{left}</div>
      <div className="hidden md:flex"><Splitter value={w} min={min} max={max} edge="right" onChange={set} onReset={reset} label={label} testid="v2-split-inner" /></div>
      <div className="min-w-0 flex-1 md:pl-2">{right}</div>
    </div>
  );
}
