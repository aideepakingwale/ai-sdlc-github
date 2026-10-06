/** Multi-model routing: helpers for the admin editor (pure, unit-tested). */

export type RoleKey = 'reason' | 'generate' | 'light' | 'plan' | 'vision';

export interface RoleView {
  role: RoleKey;
  label: string;
  description: string;
  stageSelectable: boolean;
  models: string[];        // what the admin set
  envDefault: string[];    // LIGHT_MODEL / PLAN_MODEL
  effective: string[];     // what actually serves ([] = the normal chain)
  source: 'admin' | 'env' | 'default';
}

export interface CatalogModel {
  id: string; provider: string; model: string; healthy: boolean; configured: boolean; vision: boolean;
  reason: string | null;
}

export interface ModelRoutesView {
  roles: RoleView[];
  providers: string[];
  maxChain: number;
  stageDefaults: Record<string, RoleKey>;
  catalog: CatalogModel[];
}

export interface ChainEntry { provider: string; model: string }

/** 'bedrock/us.anthropic.claude-haiku' -> { provider: 'bedrock', model: 'us.anthropic.claude-haiku' } */
export function splitModel(id: string): ChainEntry {
  const i = id.indexOf('/');
  return i > 0 ? { provider: id.slice(0, i), model: id.slice(i + 1) } : { provider: '', model: id };
}

export function joinModel(e: ChainEntry): string {
  return e.provider && e.model.trim() ? `${e.provider}/${e.model.trim()}` : '';
}

/** Drafts (one chain per role) -> the PUT body: blank rows and duplicates dropped, empty roles omitted. */
export function toPayload(draft: Record<string, ChainEntry[]>, maxChain = 4): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const [role, chain] of Object.entries(draft)) {
    const ids = [...new Set(chain.map(joinModel).filter(Boolean))].slice(0, maxChain);
    if (ids.length) out[role] = ids;
  }
  return out;
}

export function seedDraft(roles: RoleView[]): Record<string, ChainEntry[]> {
  return Object.fromEntries(roles.map((r) => [r.role, r.models.map(splitModel)]));
}

/** Has the admin changed anything relative to what is saved? */
export function isDirty(draft: Record<string, ChainEntry[]>, roles: RoleView[], maxChain = 4): boolean {
  const saved: Record<string, string[]> = {};
  for (const r of roles) if (r.models.length) saved[r.role] = r.models;
  return JSON.stringify(toPayload(draft, maxChain)) !== JSON.stringify(saved);
}

/** Move entry `i` by `delta` (-1 up, +1 down); returns a new array. */
export function move<T>(list: T[], i: number, delta: number): T[] {
  const j = i + delta;
  if (j < 0 || j >= list.length) return list;
  const next = [...list];
  [next[i], next[j]] = [next[j]!, next[i]!];
  return next;
}

/** Client-side hint for one entry: is that model known to be healthy / usable? */
export function entryHealth(e: ChainEntry, catalog: CatalogModel[], role: RoleKey): { tone: 'ok' | 'warn' | 'info'; text: string } {
  const known = catalog.find((m) => m.provider === e.provider);
  if (!e.provider || !e.model.trim()) return { tone: 'info', text: 'incomplete' };
  if (!known || !known.configured) return { tone: 'warn', text: `${e.provider} is not configured on the gateway` };
  if (!known.healthy) return { tone: 'warn', text: known.reason ?? 'provider unhealthy' };
  if (role === 'vision' && !known.vision) return { tone: 'warn', text: 'provider is not vision-capable' };
  return { tone: 'ok', text: 'provider healthy' };
}

export const STAGE_ROLE_CHOICES: Array<{ value: '' | RoleKey; label: string; hint: string }> = [
  { value: '', label: 'Automatic', hint: 'Design stages use the reasoning model, the rest the generation model' },
  { value: 'reason', label: 'Reasoning', hint: 'Strongest model - deep design work' },
  { value: 'generate', label: 'Generation', hint: 'Balanced model - general artifacts' },
  { value: 'light', label: 'Fast', hint: 'Fast model - simple, well-scoped stages' },
];
