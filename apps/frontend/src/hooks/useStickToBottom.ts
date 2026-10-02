import { useEffect, useRef, type RefObject } from 'react';
import { isNearBottom, scrollParent } from '../lib/scroll';

/**
 * "Follow the output" only while the user is already at the bottom.
 * Scroll up even a little and it stops following (no yanking back down on every streamed
 * token or log line); scroll back to the bottom and it follows again. Jumps instantly —
 * smooth scrolling on each update is what makes streaming content feel like it is fighting you.
 */
export function useStickToBottom(sentinel: RefObject<HTMLElement | null>, deps: unknown[]): void {
  const stick = useRef(false);

  useEffect(() => {
    const el = scrollParent(sentinel.current);
    if (!el) return;
    const onScroll = () => { stick.current = isNearBottom(el); };
    onScroll();
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, [sentinel]);

  useEffect(() => {
    const el = scrollParent(sentinel.current);
    if (el && stick.current) el.scrollTop = el.scrollHeight;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}
