import type { Redis } from 'ioredis';

/**
 * In-memory stand-in for the subset of ioredis the mock connectors use:
 * strings (get/set/incr), hashes, sorted sets, sets and expire.
 * Test-only: excluded from the production build.
 */
export function fakeRedis(): Redis {
  const strings = new Map<string, string>();
  const hashes = new Map<string, Map<string, string>>();
  const zsets = new Map<string, Map<string, number>>();
  const sets = new Map<string, Set<string>>();

  const bound = (v: number | string, inf: number): number => (v === '-inf' ? -Infinity : v === '+inf' ? Infinity : Number(v) || inf);

  const impl = {
    async get(key: string) {
      return strings.get(key) ?? null;
    },
    async set(key: string, value: string) {
      strings.set(key, value);
      return 'OK';
    },
    async incr(key: string) {
      const next = Number(strings.get(key) ?? 0) + 1;
      strings.set(key, String(next));
      return next;
    },
    async ping() {
      return 'PONG';
    },
    async expire() {
      return 1;
    },
    async hset(key: string, field: string, value: string) {
      const h = hashes.get(key) ?? new Map<string, string>();
      hashes.set(key, h);
      const isNew = !h.has(field);
      h.set(field, value);
      return isNew ? 1 : 0;
    },
    async hget(key: string, field: string) {
      return hashes.get(key)?.get(field) ?? null;
    },
    async hmget(key: string, ...fields: string[]) {
      const h = hashes.get(key);
      return fields.map((f) => h?.get(f) ?? null);
    },
    async zadd(key: string, score: number, member: string) {
      const z = zsets.get(key) ?? new Map<string, number>();
      zsets.set(key, z);
      const isNew = !z.has(member);
      z.set(member, Number(score));
      return isNew ? 1 : 0;
    },
    async zrangebyscore(key: string, min: number | string, max: number | string) {
      const lo = bound(min, -Infinity);
      const hi = bound(max, Infinity);
      return [...(zsets.get(key) ?? new Map<string, number>()).entries()]
        .filter(([, s]) => s >= lo && s <= hi)
        .sort((a, b) => a[1] - b[1] || (a[0] < b[0] ? -1 : 1))
        .map(([m]) => m);
    },
    async sadd(key: string, member: string) {
      const s = sets.get(key) ?? new Set<string>();
      sets.set(key, s);
      const isNew = !s.has(member);
      s.add(member);
      return isNew ? 1 : 0;
    },
    async smembers(key: string) {
      return [...(sets.get(key) ?? new Set<string>())];
    },
  };
  return impl as unknown as Redis;
}
