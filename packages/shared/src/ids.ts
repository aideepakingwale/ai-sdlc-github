import { randomBytes, randomUUID } from 'node:crypto';

/** Crockford base32 alphabet used by ULID. */
const B32 = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';

/**
 * Monotonic-enough ULID (26 chars, lexicographically time-sortable).
 * Used for S3 audit object keys so listings are chronological.
 */
export function ulid(now: number = Date.now()): string {
  let ts = now;
  const time = new Array<string>(10);
  for (let i = 9; i >= 0; i--) {
    time[i] = B32[ts % 32] as string;
    ts = Math.floor(ts / 32);
  }
  const rand = randomBytes(16);
  let out = time.join('');
  for (let i = 0; i < 16; i++) {
    out += B32[(rand[i] as number) % 32];
  }
  return out;
}

export function newId(): string {
  return randomUUID();
}
