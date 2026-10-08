import { useEffect } from 'react';
import { useV2 } from './store';

/** Below this width the sidebar becomes a drawer and the right pane covers the page. */
export const NARROW_PX = 900;

export function isNarrow(width: number): boolean { return width < NARROW_PX; }

/** Keeps `narrow` in the store in step with the window. */
export function useNarrowSync(): void {
  const setNarrow = useV2((s) => s.setNarrow);
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const mq = window.matchMedia(`(max-width: ${NARROW_PX - 1}px)`);
    const on = () => setNarrow(mq.matches);
    on();
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, [setNarrow]);
}
