import type { BreakerState } from '@sdlc/shared';

/** Minimal Redis surface the breaker needs (adapter over ioredis; in-memory fake in tests). */
export interface BreakerStore {
  get(key: string): Promise<string | null>;
  setWithTtl(key: string, value: string, ttlSeconds: number): Promise<void>;
  setPersistent(key: string, value: string): Promise<void>;
  ttl(key: string): Promise<number>;
}

/** Adapt an ioredis client to the BreakerStore interface. */
export function redisBreakerStore(redis: {
  get(key: string): Promise<string | null>;
  set(key: string, value: string, mode: 'EX', ttl: number): Promise<unknown>;
  set(key: string, value: string): Promise<unknown>;
  ttl(key: string): Promise<number>;
}): BreakerStore {
  return {
    get: (key) => redis.get(key),
    setWithTtl: async (key, value, ttl) => {
      await redis.set(key, value, 'EX', ttl);
    },
    setPersistent: async (key, value) => {
      await redis.set(key, value);
    },
    ttl: (key) => redis.ttl(key),
  };
}

const OPEN_TTL_SECONDS = 120;

/**
 * Circuit breaker with Redis-backed state shared across replicas:
 * - `open`  — provider tripped by rate-limit/transient failures; auto half-opens via TTL
 * - `disabled` — auth/billing failure (401/402/403); persists until manually cleared
 * A 5s in-process read-through cache keeps the hot path off Redis.
 */
export class CircuitBreaker {
  private cache = new Map<string, { state: BreakerState; at: number }>();

  constructor(
    private readonly store: BreakerStore,
    private readonly cacheMs = 5_000,
  ) {}

  private key(provider: string): string {
    return `breaker:${provider}`;
  }

  async state(provider: string): Promise<BreakerState> {
    const cached = this.cache.get(provider);
    if (cached && Date.now() - cached.at < this.cacheMs) return cached.state;
    const raw = await this.store.get(this.key(provider));
    const state: BreakerState = raw === 'open' || raw === 'disabled' ? raw : 'closed';
    this.cache.set(provider, { state, at: Date.now() });
    return state;
  }

  async retryInSeconds(provider: string): Promise<number | null> {
    const state = await this.state(provider);
    if (state !== 'open') return null;
    const ttl = await this.store.ttl(this.key(provider));
    return ttl > 0 ? ttl : null;
  }

  /** Trip after rate-limit exhaustion or repeated transient failure. */
  async trip(provider: string, ttlSeconds = OPEN_TTL_SECONDS): Promise<void> {
    await this.store.setWithTtl(this.key(provider), 'open', ttlSeconds);
    this.cache.set(provider, { state: 'open', at: Date.now() });
  }

  /** Permanent disable on 401/402/403 (billing/credentials). */
  async disable(provider: string): Promise<void> {
    await this.store.setPersistent(this.key(provider), 'disabled');
    this.cache.set(provider, { state: 'disabled', at: Date.now() });
  }
}
