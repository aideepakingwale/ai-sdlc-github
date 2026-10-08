import { create } from 'zustand';

export type ProjectTab = 'team' | 'artefacts' | 'files' | 'codebase' | 'audit' | 'skills';

/** What the right-hand pane shows. `null` = closed. */
export type Pane =
  | { type: 'project'; tab: ProjectTab }
  | { type: 'artefact'; id: string; from?: ProjectTab }
  | { type: 'context'; seq: number }
  | { type: 'document'; seq: number }
  | null;

interface V2State {
  pane: Pane;
  sideCollapsed: boolean;
  /** Widths (px) the user dragged the sidebar and the details pane to; the pane can also fill the whole window. */
  sideWidth: number;
  paneWidth: number;
  paneFull: boolean;
  setSideWidth: (w: number) => void;
  setPaneWidth: (w: number) => void;
  setPaneFull: (v: boolean) => void;
  /** Narrow screens: the sidebar is a drawer and the right pane covers the page. */
  narrow: boolean;
  drawer: boolean;
  setNarrow: (v: boolean) => void;
  setDrawer: (v: boolean) => void;
  openPane: (p: Pane) => void;
  closePane: () => void;
  togglePane: (p: NonNullable<Pane>) => void;
  setSideCollapsed: (v: boolean) => void;
}

const SIDE_KEY = 'sdlc:v2:side-collapsed';
export const SIDE_W = { def: 288, min: 220, max: 440 };
export const PANE_W = { def: 640, min: 340 };
const readNum = (k: string, def: number): number => { try { const n = Number(localStorage.getItem(k)); return Number.isFinite(n) && n > 0 ? n : def; } catch { return def; } };
const saveNum = (k: string, n: number) => { try { localStorage.setItem(k, String(n)); } catch { /* ignore */ } };
const readSide = (): boolean => { try { return localStorage.getItem(SIDE_KEY) === '1'; } catch { return false; } };

function same(a: Pane, b: Pane): boolean {
  if (!a || !b || a.type !== b.type) return false;
  if (a.type === 'project' && b.type === 'project') return a.tab === b.tab;
  if (a.type === 'artefact' && b.type === 'artefact') return a.id === b.id;
  if (a.type === 'context' && b.type === 'context') return a.seq === b.seq;
  if (a.type === 'document' && b.type === 'document') return a.seq === b.seq;
  return false;
}

export const useV2 = create<V2State>((set, get) => ({
  pane: null,
  sideCollapsed: readSide(),
  sideWidth: readNum('sdlc:v2:side-w', SIDE_W.def),
  paneWidth: readNum('sdlc:v2:pane-w', PANE_W.def),
  paneFull: false,
  setSideWidth: (w) => { saveNum('sdlc:v2:side-w', w); set({ sideWidth: w }); },
  setPaneWidth: (w) => { saveNum('sdlc:v2:pane-w', w); set({ paneWidth: w }); },
  setPaneFull: (v) => set({ paneFull: v }),
  narrow: false,
  drawer: false,
  setNarrow: (v) => set(v ? { narrow: true } : { narrow: false, drawer: false }),
  setDrawer: (v) => set({ drawer: v }),
  openPane: (p) => set({ pane: p }),
  closePane: () => set({ pane: null, paneFull: false }),
  togglePane: (p) => set({ pane: same(get().pane, p) ? null : p }),
  setSideCollapsed: (v) => {
    try { localStorage.setItem(SIDE_KEY, v ? '1' : '0'); } catch { /* ignore */ }
    set({ sideCollapsed: v });
  },
}));
