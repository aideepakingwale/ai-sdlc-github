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
  openPane: (p: Pane) => void;
  closePane: () => void;
  togglePane: (p: NonNullable<Pane>) => void;
  setSideCollapsed: (v: boolean) => void;
}

const SIDE_KEY = 'sdlc:v2:side-collapsed';
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
  openPane: (p) => set({ pane: p }),
  closePane: () => set({ pane: null }),
  togglePane: (p) => set({ pane: same(get().pane, p) ? null : p }),
  setSideCollapsed: (v) => {
    try { localStorage.setItem(SIDE_KEY, v ? '1' : '0'); } catch { /* ignore */ }
    set({ sideCollapsed: v });
  },
}));
