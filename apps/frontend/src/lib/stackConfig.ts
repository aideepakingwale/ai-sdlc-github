/**
 * Pure model for the Stack tab of Project Context: the shapes projectconfig.json holds, how an entry is labelled, and how the layers are
 * grouped for the screen. No React, no DOM.
 */
export type StackStatus = 'pinned' | 'identified' | 'open';
export interface StackEntry {
  id: string; layer: string; component: string; technology: string; version: string; extras: string[]; notes: string;
  status: StackStatus; source: string; sourceStage: number | null; rationale: string; evidence: string; confidence: string;
  updatedAt: string; updatedBy: string;
}
export interface StackConflict { layer: string; pinned: string; found: string; stage: number; evidence?: string }
export interface ProjectConfig {
  schemaVersion: number; version: number; updatedAt: string;
  stack: { summary: string; layers: StackEntry[]; conflicts: StackConflict[]; advisor: Record<string, { at: string; found: number; changed?: number }> };
}
export interface LayerDef { id: string; label: string; hint: string; options: string[] }
export interface StackPreset { id: string; name: string; description: string; layers: Array<Partial<StackEntry>> }

export const render = (e: Pick<StackEntry, 'technology' | 'version' | 'extras'>): string => {
  const base = [e.technology, e.version].filter(Boolean).join(' ');
  const extras = (e.extras ?? []).join(', ');
  return base && extras ? `${base} + ${extras}` : base || extras;
};

/** Where a value came from, in words a reader understands. */
export function sourceLabel(e: Pick<StackEntry, 'status' | 'source' | 'sourceStage' | 'updatedBy'>): string {
  if (e.status === 'open') return 'Left open for the Technical Architect';
  if (e.status === 'pinned') return e.source === 'preset' ? 'Pinned from a preset' : e.updatedBy ? `Pinned by ${e.updatedBy}` : 'Pinned';
  if (e.source === 'detected') return 'Detected in the uploaded codebase';
  if (e.source === 'ta') return 'Decided by the Technical Architect';
  if (e.source === 'legacy') return 'Set earlier';
  return e.sourceStage ? `Identified in stage ${e.sourceStage}` : 'Identified in the documents';
}

export const STATUS_TONE: Record<StackStatus, 'green' | 'brand' | 'slate'> = { pinned: 'green', identified: 'brand', open: 'slate' };
export const STATUS_WORD: Record<StackStatus, string> = { pinned: 'Pinned', identified: 'Identified', open: 'Open' };

export interface LayerRow { layer: LayerDef; entries: StackEntry[] }
/** One row per catalog layer (in catalog order) with its entries; entries on a layer the catalog no longer has go in a trailing row. */
export function groupLayers(layers: LayerDef[], entries: StackEntry[]): LayerRow[] {
  const rows = layers.map((layer) => ({ layer, entries: entries.filter((e) => e.layer === layer.id) }));
  const known = new Set(layers.map((l) => l.id));
  const rest = entries.filter((e) => !known.has(e.layer));
  return rest.length ? [...rows, { layer: { id: 'other', label: 'Other', hint: '', options: [] }, entries: rest }] : rows;
}

export const identifiedIds = (entries: StackEntry[]): string[] => entries.filter((e) => e.status === 'identified').map((e) => e.id);
export const decidedCount = (entries: StackEntry[]): number => entries.filter((e) => e.status !== 'open' && render(e)).length;

/** The form values for a new or edited entry, as the API's upsert expects. */
export interface EntryDraft { id?: string; layer: string; technology: string; version: string; extras: string; component: string; notes: string; status: 'pinned' | 'open' }
export function toDraft(layer: string, e?: StackEntry): EntryDraft {
  return e
    ? { id: e.id, layer: e.layer, technology: e.technology, version: e.version, extras: e.extras.join(', '), component: e.component, notes: e.notes, status: e.status === 'open' ? 'open' : 'pinned' }
    : { layer, technology: '', version: '', extras: '', component: '', notes: '', status: 'pinned' };
}
export function fromDraft(d: EntryDraft) {
  return { id: d.id, layer: d.layer, technology: d.technology.trim(), version: d.version.trim(), component: d.component.trim(), notes: d.notes.trim(), status: d.status,
    extras: d.extras.split(',').map((x) => x.trim()).filter(Boolean) };
}
export const draftError = (d: EntryDraft): string => (d.status === 'pinned' && !d.technology.trim() ? 'Say which technology it is, or leave the layer open.' : '');
