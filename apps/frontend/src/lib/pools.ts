/** Token pools (docs/token-governance.md, phase 3): a project's balance, and the organisation's. */
import { tokensLabel } from './agents';

export type PoolState = 'unmetered' | 'ok' | 'low' | 'critical' | 'empty' | 'overdrawn';

export interface PoolView {
  metered: boolean; allocated: number; consumed: number; balance: number | null; referenceTokens: number; level: number; state: PoolState;
  burnPerDay: number; monthly: number | null; meteredSince: string | null; daysLeft: number | null; runsOutOn: string | null;
}
export interface ProjectPool extends PoolView {
  projectId: string; name: string;
  organisation: { metered: boolean; balance: number | null; state: PoolState; level: number };
}
export interface PoolsOverview {
  organisation: PoolView;
  projects: Array<PoolView & { projectId: string; name: string; used30d: number; blocked7d: number }>;
  allocatedToProjects: number; unallocated: number | null; overAllocated: boolean;
}
export interface PoolDetail {
  grants: Array<{ id: string; tokens: number; kind: 'one_off' | 'monthly'; by: string; note: string; at: string }>;
  schedules: Array<{ id: string; tokens: number; nextRunAt: string; note: string }>;
}

/** "1.5M", "250k", "1,200,000" or "300000" as a whole number of tokens; null when it is not one. */
export function parseTokens(text: string): number | null {
  const t = text.trim().toLowerCase().replace(/[,_\s]/g, '');
  const m = /^(-?\d+(?:\.\d+)?)([kmb]?)$/.exec(t);
  if (!m) return null;
  const mult = { '': 1, k: 1e3, m: 1e6, b: 1e9 }[m[2] as '' | 'k' | 'm' | 'b'];
  const n = Math.round(Number(m[1]) * mult);
  return Number.isSafeInteger(n) ? n : null;
}

/** Tokens with a sign when negative ("−12k"), short for large numbers. */
export function signedTokens(n: number): string {
  return n < 0 ? `−${tokensLabel(-n)}` : tokensLabel(n);
}

export const STATE_TONE: Record<PoolState, 'slate' | 'green' | 'amber' | 'red'> = { unmetered: 'slate', ok: 'green', low: 'amber', critical: 'red', empty: 'red', overdrawn: 'red' };
export const STATE_LABEL: Record<PoolState, string> = { unmetered: 'Not metered', ok: 'Healthy', low: 'Running low', critical: 'Almost out', empty: 'Used up', overdrawn: 'Overdrawn' };

/** How much of what was allocated has been used, 0..1 (more than 1 when overdrawn). */
export const usedShare = (p: { allocated: number; consumed: number }): number => (p.allocated > 0 ? p.consumed / p.allocated : p.consumed > 0 ? 1 : 0);

/** The line beside the meter: what is left and, when it can be told, for how long. */
export function balanceLine(p: Pick<PoolView, 'metered' | 'balance' | 'daysLeft' | 'runsOutOn' | 'state'>): string {
  if (!p.metered || p.balance === null) return 'Tokens are not limited for this project yet.';
  if (p.balance < 0) return `Overdrawn by ${tokensLabel(-p.balance)} tokens`;
  if (p.balance === 0) return 'No tokens left';
  const left = `${tokensLabel(p.balance)} tokens left`;
  if (p.daysLeft === null) return left;
  if (p.daysLeft >= 90) return `${left} · more than 3 months at this pace`;
  return p.daysLeft < 1 ? `${left} · about a day at this pace` : `${left} · about ${Math.round(p.daysLeft)} ${Math.round(p.daysLeft) === 1 ? 'day' : 'days'} at this pace`;
}

/** What the organisation's pool means for a project: only worth saying when it is the thing running out. */
export function orgNote(p: ProjectPool): string | null {
  const o = p.organisation;
  if (!o.metered || o.state === 'ok' || o.state === 'unmetered') return null;
  return o.state === 'low' || o.state === 'critical' ? 'The organisation’s tokens are running low.' : 'The organisation’s tokens are used up.';
}

const ATTENTION: Record<PoolState, number> = { overdrawn: 0, empty: 1, critical: 2, low: 3, ok: 4, unmetered: 5 };
/** Projects needing attention first (overdrawn, then empty, critical, low), then healthy ones, then those with no allocation, busiest first within each. */
export function sortPools<T extends { state: PoolState; used30d: number; name: string }>(rows: T[]): T[] {
  return [...rows].sort((a, b) => ATTENTION[a.state] - ATTENTION[b.state] || b.used30d - a.used30d || a.name.localeCompare(b.name));
}

export interface TokenRequest { id: string; projectId: string; project: string; by: string; note: string; at: string; balance: number | null }

/** True when the project's balance (or the organisation's) means new work will be refused. */
export function isBlocked(p: Pick<ProjectPool, 'state' | 'organisation'>): boolean {
  return p.state === 'empty' || p.state === 'overdrawn' || p.organisation.state === 'empty' || p.organisation.state === 'overdrawn';
}

export type GateMode = 'observe' | 'enforce' | 'off';
export interface GateStatus { mode: GateMode; default: GateMode; options: GateMode[]; refused7d: number; wouldRefuse7d: number }
export const GATE_LABEL: Record<GateMode, string> = { observe: 'Observe only', enforce: 'Enforce', off: 'Off' };

/** What the gate is doing, in a sentence an administrator can act on. */
export function gateSentence(g: GateStatus): string {
  const n = (x: number) => `${x} start${x === 1 ? '' : 's'}`;
  if (g.mode === 'observe') return g.wouldRefuse7d > 0 ? `Nothing is blocked yet. In the last 7 days it would have refused ${n(g.wouldRefuse7d)}.` : 'Nothing is blocked yet, and in the last 7 days nothing would have been.';
  if (g.mode === 'enforce') return g.refused7d > 0 ? `New work is refused when a pool is at zero or below. It refused ${n(g.refused7d)} in the last 7 days.` : 'New work is refused when a pool is at zero or below. Nothing was refused in the last 7 days.';
  return 'The gate is off: tokens are counted but never block anything.';
}
