export type PartStatus = 'running' | 'done' | 'failed';

const RANK: Record<PartStatus, number> = { running: 0, failed: 1, done: 2 };

/**
 * Tab order while artifacts generate: what is being worked on right now comes first,
 * failed ones next (they need attention), finished ones go to the end. Stable within a
 * group, so tabs keep their arrival order and don't shuffle against each other.
 */
export function orderParts<T extends { status: PartStatus }>(parts: T[]): T[] {
  return parts
    .map((p, i) => ({ p, i }))
    .sort((a, b) => RANK[a.p.status] - RANK[b.p.status] || a.i - b.i)
    .map((x) => x.p);
}
