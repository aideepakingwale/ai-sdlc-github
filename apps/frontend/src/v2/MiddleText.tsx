import { useLayoutEffect, useRef, useState } from 'react';
import { fitMiddle } from '../lib/middleTruncate';

/** One line that shortens itself in the middle when it does not fit ("src/main/…/OrderService.java"); hover shows all of it. */
export default function MiddleText({ text, title, className = '' }: { text: string; title?: string; className?: string }) {
  const box = useRef<HTMLSpanElement>(null);
  const probe = useRef<HTMLSpanElement>(null);
  const [shown, setShown] = useState(text);
  useLayoutEffect(() => {
    const el = box.current, pr = probe.current;
    if (!el || !pr) return;
    const run = () => {
      const width = el.clientWidth;
      if (width <= 0) return;
      setShown(fitMiddle(text, (c) => { pr.textContent = c; return pr.offsetWidth <= width; }));
      pr.textContent = '';
    };
    run();
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(run) : null;
    ro?.observe(el);
    return () => ro?.disconnect();
  }, [text]);
  return (
    <span ref={box} className={`relative block min-w-0 overflow-hidden whitespace-nowrap ${className}`} title={title ?? text} data-full={text}>
      {shown}
      <span ref={probe} aria-hidden="true" className="invisible absolute left-0 top-0 whitespace-nowrap" />
    </span>
  );
}
