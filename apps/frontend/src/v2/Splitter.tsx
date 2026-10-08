import { useRef } from 'react';

/**
 * A draggable divider between two panes. `edge` says which side of the resized pane it sits on:
 * 'right' for a pane to its left (the sidebar), 'left' for a pane to its right (the details pane).
 * Drag, use the arrow keys, or double-click to reset.
 */
export default function Splitter({ value, min, max, edge, onChange, onReset, label, testid }: {
  value: number; min: number; max: number; edge: 'left' | 'right'; onChange: (w: number) => void; onReset?: () => void; label: string; testid?: string;
}) {
  const start = useRef<{ x: number; w: number } | null>(null);
  const clamp = (w: number) => Math.round(Math.min(max, Math.max(min, w)));
  return (
    <div
      role="separator" aria-orientation="vertical" aria-label={label} aria-valuemin={min} aria-valuemax={max} aria-valuenow={value} tabIndex={0} data-testid={testid}
      title="Drag to resize · double-click to reset"
      className="group relative z-10 w-1.5 shrink-0 cursor-col-resize touch-none bg-transparent outline-none hover:bg-brand-200 focus-visible:bg-brand-300"
      onPointerDown={(e) => { e.preventDefault(); e.currentTarget.setPointerCapture(e.pointerId); start.current = { x: e.clientX, w: value }; document.body.style.userSelect = 'none'; }}
      onPointerMove={(e) => {
        const s = start.current; if (!s) return;
        const dx = e.clientX - s.x;
        onChange(clamp(edge === 'right' ? s.w + dx : s.w - dx));
      }}
      onPointerUp={(e) => { start.current = null; document.body.style.userSelect = ''; e.currentTarget.releasePointerCapture?.(e.pointerId); }}
      onPointerCancel={() => { start.current = null; document.body.style.userSelect = ''; }}
      onDoubleClick={onReset}
      onKeyDown={(e) => {
        const step = e.shiftKey ? 48 : 16;
        const grow = edge === 'right' ? 'ArrowRight' : 'ArrowLeft', shrink = edge === 'right' ? 'ArrowLeft' : 'ArrowRight';
        if (e.key === grow) { e.preventDefault(); onChange(clamp(value + step)); }
        else if (e.key === shrink) { e.preventDefault(); onChange(clamp(value - step)); }
        else if (e.key === 'Home') { e.preventDefault(); onChange(min); }
        else if (e.key === 'End') { e.preventDefault(); onChange(max); }
      }}
    >
      <span aria-hidden="true" className="pointer-events-none absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-slate-200 group-hover:bg-brand-400 group-focus-visible:bg-brand-500" />
    </div>
  );
}
