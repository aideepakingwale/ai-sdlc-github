import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';

/**
 * Workspace pane chrome: every major area of the project workspace
 * (pipeline flow, run visualiser, skills, gate review, chat) is wrapped in a
 * Panel so the user can focus on one area — collapse it, drag its bottom edge
 * to resize, or maximise it to the full viewport. Collapsed/height state is
 * persisted per panel so the layout survives reloads.
 */

interface PanelState {
  collapsed: boolean;
  height: number | null; // null = natural height
}

function loadState(id: string): PanelState {
  try {
    const raw = localStorage.getItem(`sdlc:panel:${id}`);
    if (raw) return JSON.parse(raw) as PanelState;
  } catch {
    /* ignore corrupt state */
  }
  return { collapsed: false, height: null };
}

export default function Panel({
  id,
  title,
  subtitle,
  children,
  actions,
  defaultHeight = null,
  minHeight = 96,
  resizable = true,
  className = '',
}: {
  id: string;
  title: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
  actions?: ReactNode;
  defaultHeight?: number | null;
  minHeight?: number;
  resizable?: boolean;
  className?: string;
}) {
  const [state, setState] = useState<PanelState>(() => {
    const saved = loadState(id);
    return { ...saved, height: saved.height ?? defaultHeight };
  });
  const [maximized, setMaximized] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try {
      localStorage.setItem(`sdlc:panel:${id}`, JSON.stringify(state));
    } catch {
      /* storage full / disabled — layout just won't persist */
    }
  }, [id, state]);

  // Esc leaves full-screen focus.
  useEffect(() => {
    if (!maximized) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setMaximized(false);
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [maximized]);

  const startResize = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      const startY = e.clientY;
      const startH = bodyRef.current?.getBoundingClientRect().height ?? minHeight;
      const target = e.currentTarget as HTMLElement;
      target.setPointerCapture(e.pointerId);

      const onMove = (ev: PointerEvent) => {
        const next = Math.max(minHeight, startH + (ev.clientY - startY));
        setState((s) => ({ ...s, height: next, collapsed: false }));
      };
      const onUp = (ev: PointerEvent) => {
        target.releasePointerCapture?.(ev.pointerId);
        target.removeEventListener('pointermove', onMove);
        target.removeEventListener('pointerup', onUp);
      };
      target.addEventListener('pointermove', onMove);
      target.addEventListener('pointerup', onUp);
    },
    [minHeight],
  );

  const header = (
    <div className="flex items-center gap-2 border-b border-slate-200 bg-white px-3 py-1.5">
      <button
        onClick={() => setState((s) => ({ ...s, collapsed: !s.collapsed }))}
        className="rounded px-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
        title={state.collapsed ? 'Expand' : 'Collapse'}
        aria-expanded={!state.collapsed}
      >
        {state.collapsed ? '▸' : '▾'}
      </button>
      <span className="truncate text-[11px] font-bold uppercase tracking-wide text-slate-600">{title}</span>
      {subtitle && <span className="truncate text-[10px] text-slate-400">{subtitle}</span>}
      <div className="ml-auto flex items-center gap-1">
        {actions}
        {state.height !== null && !maximized && (
          <button
            onClick={() => setState((s) => ({ ...s, height: null }))}
            className="rounded px-1.5 py-0.5 text-[10px] text-slate-400 hover:bg-slate-100 hover:text-slate-600"
            title="Reset to natural height"
          >
            ⤢ reset
          </button>
        )}
        <button
          onClick={() => setMaximized((m) => !m)}
          className="rounded px-1.5 py-0.5 text-xs text-slate-400 hover:bg-slate-100 hover:text-brand-600"
          title={maximized ? 'Restore (Esc)' : 'Maximise to full screen'}
        >
          {maximized ? '🗗' : '🗖'}
        </button>
      </div>
    </div>
  );

  if (maximized) {
    return (
      <div className="fixed inset-0 z-40 flex flex-col bg-white">
        {header}
        <div className="min-h-0 flex-1 overflow-auto">{children}</div>
      </div>
    );
  }

  return (
    <div className={`flex flex-col border-b border-slate-200 bg-white ${className}`}>
      {header}
      {!state.collapsed && (
        <>
          <div
            ref={bodyRef}
            className="overflow-auto"
            style={state.height !== null ? { height: state.height } : undefined}
          >
            {children}
          </div>
          {resizable && (
            <div
              onPointerDown={startResize}
              onDoubleClick={() => setState((s) => ({ ...s, height: null }))}
              className="group h-1.5 shrink-0 cursor-row-resize bg-slate-100 hover:bg-brand-200"
              title="Drag to resize · double-click to reset"
            >
              <div className="mx-auto h-0.5 w-8 translate-y-0.5 rounded bg-slate-300 group-hover:bg-brand-400" />
            </div>
          )}
        </>
      )}
    </div>
  );
}

/**
 * Draggable vertical splitter for a side panel's width. Persists to
 * localStorage under the given id.
 */
export function useResizableWidth(id: string, initial: number, min = 220, max = 720) {
  const [width, setWidth] = useState<number>(() => {
    const raw = Number(localStorage.getItem(`sdlc:width:${id}`));
    return Number.isFinite(raw) && raw >= min && raw <= max ? raw : initial;
  });

  useEffect(() => {
    try {
      localStorage.setItem(`sdlc:width:${id}`, String(width));
    } catch {
      /* ignore */
    }
  }, [id, width]);

  /** `side` says which edge the handle sits on, so the drag direction is right. */
  const onPointerDown = useCallback(
    (e: React.PointerEvent, side: 'left' | 'right' = 'left') => {
      e.preventDefault();
      const startX = e.clientX;
      const startW = width;
      const target = e.currentTarget as HTMLElement;
      target.setPointerCapture(e.pointerId);

      const onMove = (ev: PointerEvent) => {
        const delta = side === 'left' ? startX - ev.clientX : ev.clientX - startX;
        setWidth(Math.min(max, Math.max(min, startW + delta)));
      };
      const onUp = (ev: PointerEvent) => {
        target.releasePointerCapture?.(ev.pointerId);
        target.removeEventListener('pointermove', onMove);
        target.removeEventListener('pointerup', onUp);
      };
      target.addEventListener('pointermove', onMove);
      target.addEventListener('pointerup', onUp);
    },
    [width, min, max],
  );

  return { width, setWidth, onPointerDown };
}
