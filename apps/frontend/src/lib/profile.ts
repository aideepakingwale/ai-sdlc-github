/**
 * Pure model for the project profile, rule packs and the advice shown on stages 2 and 3: shapes, grouping, labels and filters.
 * No React, no DOM.
 */
export type ProfileKind = 'industry' | 'regulation' | 'domain' | 'sensitivity' | 'region';
export type ProfileState = 'pinned' | 'identified' | 'inherited';
export interface ProfileItem { id: string; kind: ProfileKind; value: string; label: string; state: ProfileState; origin: 'project' | 'organisation'; evidence: string; sourceStage: number | null }
export interface VocabEntry { id: string; label: string }
export interface ProfileResponse {
  items: Array<{ id: string; kind: ProfileKind; value: string; status: 'pinned' | 'identified' | 'excluded' }>;
  org: Array<{ id: string; kind: ProfileKind; value: string }>;
  effective: { items: ProfileItem[]; values: Record<ProfileKind, string[]> };
  canEdit: boolean; vocab: Record<ProfileKind, VocabEntry[]>; kinds: Record<ProfileKind, { label: string; single: boolean }>; text: string;
}

export const KIND_ORDER: ProfileKind[] = ['industry', 'regulation', 'domain', 'sensitivity', 'region'];
export const STATE_WORD: Record<ProfileState, string> = { pinned: 'Set by you', identified: 'Found in your documents', inherited: 'From the organisation' };
export const STATE_TONE: Record<ProfileState, 'green' | 'brand' | 'slate'> = { pinned: 'green', identified: 'brand', inherited: 'slate' };

/** Words for where a profile value came from, with the stage when the platform found it. */
export function stateText(i: Pick<ProfileItem, 'state' | 'sourceStage'>): string {
  return i.state === 'identified' && i.sourceStage ? `Found in stage ${i.sourceStage}` : STATE_WORD[i.state];
}

export const itemsOf = (items: ProfileItem[], kind: ProfileKind): ProfileItem[] => items.filter((i) => i.kind === kind);
export const unconfirmed = (items: ProfileItem[]): string[] => items.filter((i) => i.state === 'identified').map((i) => i.id);

// ---- packs
export type PackKind = 'practice' | 'regulation' | 'industry' | 'bundle';
export interface PackView {
  id: string; name: string; description: string; kind: PackKind; kindLabel: string; version: number; baseline: boolean;
  tags: { industries: string[]; regulations: string[]; domains: string[] }; includes: Array<{ id: string; name: string }>;
  count: number; alreadyHave: number; source: string; stages: number[];
}
export const KIND_FILTERS: Array<{ id: PackKind | 'all'; label: string }> = [
  { id: 'all', label: 'All' }, { id: 'bundle', label: 'Ready-made sets' }, { id: 'industry', label: 'Industries' }, { id: 'regulation', label: 'Regulations' }, { id: 'practice', label: 'Practices' },
];

export function filterPacks(packs: PackView[], kind: PackKind | 'all', query: string): PackView[] {
  const q = query.trim().toLowerCase();
  return packs.filter((p) => (kind === 'all' || p.kind === kind) && (!q || `${p.name} ${p.description} ${[...p.tags.industries, ...p.tags.regulations, ...p.tags.domains].join(' ')}`.toLowerCase().includes(q)));
}

export const packState = (p: Pick<PackView, 'count' | 'alreadyHave'>): 'applied' | 'partial' | 'none' => (p.alreadyHave >= p.count ? 'applied' : p.alreadyHave > 0 ? 'partial' : 'none');
export const packButton = (p: Pick<PackView, 'count' | 'alreadyHave'>): string => ({ applied: 'Added', partial: `Add the other ${p.count - p.alreadyHave}`, none: `Add ${p.count} rules` })[packState(p)];

export interface Recommended { id: string; name: string; kind: PackKind; description: string; version: number; score: number; reasons: string[]; total: number; have: number; missing: number; includes: Array<{ id: string; name: string }> }
export interface Recommendations {
  canAuthor: boolean; stage: number | null; due: boolean; dismissed: boolean;
  profile: { items: ProfileItem[]; text: string; empty: boolean; unconfirmed: number };
  primary: Recommended | null; bundles: Recommended[]; packs: Recommended[]; baseline: Recommended[]; todo: string[];
}

/** What the advice card offers: the best ready-made set when there is one, else the matching packs and the essentials, never what is complete. */
export function adviceItems(r: Recommendations): Recommended[] {
  const list = r.primary ? [r.primary, ...r.packs] : [...r.packs, ...r.baseline];
  return list.filter((x) => x.missing > 0).slice(0, 4);
}
export const adviceIds = (r: Recommendations): string[] => (r.primary && r.primary.missing > 0 ? [r.primary.id] : adviceItems(r).map((x) => x.id));
