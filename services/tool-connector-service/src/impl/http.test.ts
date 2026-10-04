import { describe, expect, it } from 'vitest';
import { backoffMs, httpRequest, parseRetryAfterMs, resolvePolicy, type HttpPolicy } from './http.js';
import { mapLimit } from './concurrency.js';
import { githubRateLimitDetector } from './github-api.js';

type Step = { status: number; headers?: Record<string, string>; body?: string } | Error;

function scripted(steps: Step[]): { policy: HttpPolicy; calls: number; sleeps: number[] } {
  const state = { calls: 0, sleeps: [] as number[] };
  const policy = resolvePolicy({
    maxRetries: 3,
    baseDelayMs: 100,
    maxDelayMs: 1_000,
    random: () => 0,
    sleep: async (ms) => {
      state.sleeps.push(ms);
    },
    fetch: async () => {
      const step = steps[Math.min(state.calls++, steps.length - 1)] as Step;
      if (step instanceof Error) throw step;
      return new Response(step.body ?? '{}', { status: step.status, headers: step.headers });
    },
  });
  return Object.assign(state, { policy }) as unknown as { policy: HttpPolicy; calls: number; sleeps: number[] };
}

const get = { url: 'http://x/y', label: 'Test' };
const post = { url: 'http://x/y', label: 'Test', method: 'POST', body: '{}' };

describe('parseRetryAfterMs / backoff', () => {
  it('parses seconds, dates and garbage', () => {
    expect(parseRetryAfterMs('2')).toBe(2000);
    expect(parseRetryAfterMs('0')).toBe(0);
    expect(parseRetryAfterMs('1.5')).toBe(1500);
    expect(parseRetryAfterMs(null)).toBeNull();
    expect(parseRetryAfterMs('soon')).toBeNull();
    expect(parseRetryAfterMs(new Date(10_000).toUTCString(), 4_000)).toBe(6000);
    expect(parseRetryAfterMs(new Date(1_000).toUTCString(), 4_000)).toBe(0);
  });

  it('backs off exponentially with bounded jitter and a cap', () => {
    const p = { baseDelayMs: 100, maxDelayMs: 1_000, random: () => 0 };
    expect([0, 1, 2, 3, 4, 10].map((a) => backoffMs(a, p))).toEqual([50, 100, 200, 400, 500, 500]);
    expect(backoffMs(2, { ...p, random: () => 0.999 })).toBeLessThanOrEqual(400);
    expect(backoffMs(2, { ...p, random: () => 0.999 })).toBeGreaterThanOrEqual(200);
  });
});

describe('httpRequest retry rules', () => {
  it('retries 429 for every method and honours Retry-After', async () => {
    const s = scripted([{ status: 429, headers: { 'retry-after': '3' } }, { status: 200, body: '{"ok":true}' }]);
    const res = await httpRequest(post, s.policy);
    expect(res.status).toBe(200);
    expect(s.calls).toBe(2);
    expect(s.sleeps[0]).toBeGreaterThanOrEqual(3000);
    expect(s.sleeps[0]).toBeLessThan(3200);
  });

  it('retries 502/503/504 for idempotent requests with growing delays', async () => {
    const s = scripted([{ status: 503 }, { status: 502 }, { status: 504 }, { status: 200 }]);
    expect((await httpRequest(get, s.policy)).status).toBe(200);
    expect(s.sleeps).toEqual([50, 100, 200]);
  });

  it('does NOT retry 5xx for non-idempotent POST (the write may have happened)', async () => {
    const s = scripted([{ status: 503 }, { status: 200 }]);
    const res = await httpRequest(post, s.policy);
    expect(res.status).toBe(503);
    expect(s.calls).toBe(1);
  });

  it('retries a POST that is declared idempotent', async () => {
    const s = scripted([{ status: 503 }, { status: 200 }]);
    expect((await httpRequest({ ...post, idempotent: true }, s.policy)).status).toBe(200);
  });

  it('does not retry client errors', async () => {
    for (const status of [400, 401, 403, 404, 409, 422]) {
      const s = scripted([{ status }, { status: 200 }]);
      expect((await httpRequest(get, s.policy)).status).toBe(status);
      expect(s.calls).toBe(1);
    }
  });

  it('gives up after maxRetries and returns the last response', async () => {
    const s = scripted([{ status: 503 }]);
    const res = await httpRequest(get, s.policy);
    expect(res.status).toBe(503);
    expect(s.calls).toBe(4); // 1 + 3 retries
  });

  it('does not wait on an absurd Retry-After; returns the response instead', async () => {
    const s = scripted([{ status: 429, headers: { 'retry-after': '3600' } }, { status: 200 }]);
    expect((await httpRequest(get, s.policy)).status).toBe(429);
    expect(s.calls).toBe(1);
    expect(s.sleeps).toEqual([]);
  });

  it('retries network errors for idempotent requests only', async () => {
    const s = scripted([new TypeError('fetch failed'), { status: 200 }]);
    expect((await httpRequest(get, s.policy)).status).toBe(200);
    const t = scripted([new TypeError('fetch failed'), { status: 200 }]);
    await expect(httpRequest(post, t.policy)).rejects.toMatchObject({ code: 'TOOL_ERROR', message: expect.stringContaining('fetch failed') as unknown });
    expect(t.calls).toBe(1);
  });

  it('reports exhausted network errors as TOOL_ERROR without leaking headers', async () => {
    const s = scripted([new TypeError('boom')]);
    const err = await httpRequest({ ...get, headers: { authorization: 'Bearer SECRET' } }, s.policy).catch((e: unknown) => e);
    expect(err).toMatchObject({ code: 'TOOL_ERROR' });
    expect(String((err as Error).message)).not.toContain('SECRET');
  });

  it('times out hung requests', async () => {
    const policy = resolvePolicy({
      maxRetries: 0,
      timeoutMs: 30,
      fetch: (_url, init) =>
        new Promise<Response>((_resolve, reject) => {
          (init?.signal as AbortSignal).addEventListener('abort', () => reject((init?.signal as AbortSignal).reason));
        }),
    });
    await expect(httpRequest(get, policy)).rejects.toMatchObject({ code: 'TOOL_ERROR', message: expect.stringContaining('timed out') as unknown });
  });

  it('json() fails clearly on non-JSON bodies', async () => {
    const s = scripted([{ status: 200, body: '<html>' }]);
    const res = await httpRequest(get, s.policy);
    await expect(res.json()).rejects.toMatchObject({ code: 'TOOL_ERROR' });
  });
});

describe('GitHub rate-limit detection', () => {
  const h = (init: Record<string, string> = {}) => new Headers(init);
  it('treats 429 and rate-limited 403 as retryable, plain 403 as not', () => {
    expect(githubRateLimitDetector(429, h(), '')).toBe(true);
    expect(githubRateLimitDetector(403, h({ 'retry-after': '1' }), '')).toBe(true);
    expect(githubRateLimitDetector(403, h({ 'x-ratelimit-remaining': '0' }), '')).toBe(true);
    expect(githubRateLimitDetector(403, h(), '{"message":"You have exceeded a secondary rate limit"}')).toBe(true);
    expect(githubRateLimitDetector(403, h(), '{"message":"Resource not accessible by integration"}')).toBe(false);
    expect(githubRateLimitDetector(500, h(), 'rate limit')).toBe(false);
  });
});

describe('mapLimit', () => {
  it('bounds concurrency, preserves order and propagates the first failure', async () => {
    let inflight = 0;
    let peak = 0;
    const out = await mapLimit([1, 2, 3, 4, 5, 6, 7, 8, 9], 3, async (n) => {
      inflight++;
      peak = Math.max(peak, inflight);
      await new Promise((r) => setTimeout(r, 5));
      inflight--;
      return n * 2;
    });
    expect(out).toEqual([2, 4, 6, 8, 10, 12, 14, 16, 18]);
    expect(peak).toBe(3);
    await expect(mapLimit([1, 2, 3], 2, async (n) => { if (n === 2) throw new Error('bad'); return n; })).rejects.toThrow('bad');
    expect(await mapLimit([], 2, async () => 1)).toEqual([]);
    await expect(mapLimit([1], 0, async () => 1)).rejects.toThrow(RangeError);
  });
});
