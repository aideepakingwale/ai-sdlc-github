import { pino } from 'pino';
import { describe, expect, it } from 'vitest';
import { SdlcError, type GenerateRequest } from '@sdlc/shared';
import { CircuitBreaker, type BreakerStore } from './breaker.js';
import { makeUsage, ProviderCallError, type LlmProvider } from './providers/types.js';
import { LlmRouter } from './router.js';

/** Token governance: the router must report EVERY provider call, billed failures included. */
const store = (): BreakerStore => {
  const m = new Map<string, string>();
  return {
    async get(k) { return m.get(k) ?? null; },
    async setWithTtl(k, v) { m.set(k, v); },
    async setPersistent(k, v) { m.set(k, v); },
    async ttl() { return -1; },
    async del(k) { m.delete(k); },
  };
};
const req: GenerateRequest = {
  intent: 'generation', messages: [{ role: 'user', content: 'hi' }], json: false, temperature: 0.2, maxTokens: 64, tier: 'auto',
};
const router = (providers: LlmProvider[]) =>
  new LlmRouter(providers, new CircuitBreaker(store(), 0), pino({ level: 'silent' }), { forceMock: false, allowMockFallback: false });

const fails = (id: 'bedrock' | 'groq', kind: ProviderCallError['kind'], tokens: number): LlmProvider => ({
  id, configured: true, model: `${id}-m`,
  async generate() {
    throw new ProviderCallError(id, kind, `${id} ${kind}`, undefined, {
      usage: tokens ? makeUsage(tokens, tokens) : undefined, providerRequestId: `${id}-req`, stopReason: kind === 'canceled' ? undefined : 'x',
    });
  },
});
const works = (id: 'bedrock' | 'groq' | 'gemini'): LlmProvider => ({
  id, configured: true, model: `${id}-m`,
  async generate() {
    return { content: 'done', model: `${id}-m`, usage: makeUsage(10, 5, { cacheRead: 20 }), providerRequestId: `${id}-ok`, stopReason: 'end_turn', region: 'r' };
  },
});

describe('usage log', () => {
  it('records a single successful call', async () => {
    const res = await router([works('bedrock')]).generate(req, new AbortController().signal);
    expect(res.usageLog).toHaveLength(1);
    expect(res.usageLog[0]).toMatchObject({ provider: 'bedrock', outcome: 'ok', providerRequestId: 'bedrock-ok', stopReason: 'end_turn', region: 'r' });
    expect(res.usageLog[0]!.usage.promptTokens).toBe(30);
  });

  it('records the billed failure before a failover as its own attempt', async () => {
    const res = await router([fails('bedrock', 'auth_or_billing', 500), works('gemini')]).generate(req, new AbortController().signal);
    expect(res.provider).toBe('gemini');
    expect(res.usageLog.map((a) => [a.provider, a.outcome])).toEqual([['bedrock', 'error'], ['gemini', 'ok']]);
    expect(res.usageLog[0]!.usage.completionTokens).toBe(500);
    expect(res.usageLog[0]!.errorKind).toBe('auth_or_billing');
  });

  it('records every retry on the same provider', async () => {
    let n = 0;
    const flaky: LlmProvider = {
      id: 'groq', configured: true, model: 'g',
      async generate() {
        n += 1;
        if (n === 1) throw new ProviderCallError('groq', 'rate_limit', '429', 429, { usage: makeUsage(3, 0) });
        return { content: 'ok', model: 'g', usage: makeUsage(4, 2) };
      },
    };
    const res = await router([flaky]).generate(req, new AbortController().signal);
    expect(res.usageLog.map((a) => a.outcome)).toEqual(['error', 'ok']);
    expect(res.usageLog[0]!.usage.promptTokens).toBe(3);
  });

  it('puts the log on PROVIDER_EXHAUSTED so failed spend is not lost', async () => {
    const err = await router([fails('bedrock', 'auth_or_billing', 7), fails('groq', 'auth_or_billing', 0)])
      .generate(req, new AbortController().signal).catch((e) => e);
    expect(err).toBeInstanceOf(SdlcError);
    const log = (err.details as { usageLog: Array<{ provider: string; usage: { promptTokens: number } }> }).usageLog;
    expect(log.map((a) => a.provider)).toEqual(['bedrock', 'groq']);
    expect(log[0]!.usage.promptTokens).toBe(7);
  });

  it('a cancelled call carries its partial usage on the error', async () => {
    const err = await router([fails('bedrock', 'canceled', 9)]).generate(req, new AbortController().signal).catch((e) => e);
    expect(err).toBeInstanceOf(ProviderCallError);
    expect((err as ProviderCallError).usageLog?.[0]).toMatchObject({ outcome: 'canceled', provider: 'bedrock' });
    expect((err as ProviderCallError).usageLog?.[0]!.usage.completionTokens).toBe(9);
  });

  it('marks a refusal', async () => {
    const refuse: LlmProvider = {
      id: 'bedrock', configured: true, model: 'b',
      async generate() { throw new ProviderCallError('bedrock', 'auth_or_billing', 'no text', undefined, { usage: makeUsage(40, 5), stopReason: 'refusal' }); },
    };
    const err = await router([refuse]).generate(req, new AbortController().signal).catch((e) => e);
    expect((err.details as { usageLog: Array<{ outcome: string }> }).usageLog[0]!.outcome).toBe('refused');
  });
});
