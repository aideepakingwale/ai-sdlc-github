import { afterEach, describe, expect, it, vi } from 'vitest';
import type { GenerateRequest } from '@sdlc/shared';
import { createOpenAiCompatProvider } from './openai-compat.js';
import { classifyStatus, fitMaxTokens } from './types.js';

const base: GenerateRequest = {
  intent: 'generation',
  messages: [{ role: 'user', content: 'Describe the architecture.' }],
  json: true,
  temperature: 0.2,
  maxTokens: 128,
  tier: 'auto',
};

function stubFetch() {
  const spy = vi.fn((_url: string, _init: RequestInit) =>
    Promise.resolve(
      new Response(JSON.stringify({ choices: [{ message: { content: '{}' } }], model: 'm' }), { status: 200 }),
    ),
  );
  vi.stubGlobal('fetch', spy);
  return spy;
}

function bodyOf(spy: ReturnType<typeof stubFetch>): { messages: Array<{ role: string; content: string }>; response_format?: unknown } {
  return JSON.parse(spy.mock.calls[0]![1].body as string);
}

afterEach(() => vi.unstubAllGlobals());

describe('openai-compat json_object mode', => {
  it('injects a "json" mention when the prompt lacks one (Groq 400 guard)', async () => {
    const spy = stubFetch();
    const p = createOpenAiCompatProvider({ id: 'groq', baseUrl: 'http://x', apiKey: 'k', model: 'm' });
    await p.generate(base, new AbortController().signal);
    const body = bodyOf(spy);
    expect(body.response_format).toEqual({ type: 'json_object' });
    expect(body.messages.some((m) => /json/i.test(m.content))).toBe(true);
    // original user message is preserved
    expect(body.messages.at(-1)!.content).toBe('Describe the architecture.');
  });

  it('does not add a mention when the prompt already says json', async () => {
    const spy = stubFetch();
    const p = createOpenAiCompatProvider({ id: 'groq', baseUrl: 'http://x', apiKey: 'k', model: 'm' });
    await p.generate({ ...base, messages: [{ role: 'user', content: 'Return JSON now.' }] }, new AbortController().signal);
    expect(bodyOf(spy).messages).toHaveLength(1);
  });

  it('leaves messages untouched for non-json requests', async () => {
    const spy = stubFetch();
    const p = createOpenAiCompatProvider({ id: 'groq', baseUrl: 'http://x', apiKey: 'k', model: 'm' });
    await p.generate({ ...base, json: false }, new AbortController().signal);
    const body = bodyOf(spy);
    expect(body.response_format).toBeUndefined();
    expect(body.messages).toHaveLength(1);
  });
});

describe('request token budget', => {
  it('caps max_tokens so a large prompt fits the budget', async () => {
    const spy = stubFetch();
    const p = createOpenAiCompatProvider({
      id: 'groq', baseUrl: 'http://x', apiKey: 'k', model: 'm', requestTokenBudget: 12_000,
    });
    // ~10k-token prompt (40k chars): budget leaves ~1744 for completion.
    const prompt = 'x'.repeat(40_000);
    await p.generate(
      { ...base, json: false, maxTokens: 8192, messages: [{ role: 'user', content: prompt }] },
      new AbortController().signal,
    );
    const sent = JSON.parse((spy.mock.calls[0]![1].body as string)) as { max_tokens: number };
    expect(sent.max_tokens).toBeLessThan(8192);
    expect(sent.max_tokens).toBeGreaterThan(0);
  });

  it('leaves max_tokens alone when the prompt fits comfortably', async () => {
    const spy = stubFetch();
    const p = createOpenAiCompatProvider({
      id: 'groq', baseUrl: 'http://x', apiKey: 'k', model: 'm', requestTokenBudget: 12_000,
    });
    await p.generate({ ...base, json: false, maxTokens: 1024 }, new AbortController().signal);
    const sent = JSON.parse((spy.mock.calls[0]![1].body as string)) as { max_tokens: number };
    expect(sent.max_tokens).toBe(1024);
  });
});

describe('fitMaxTokens', () => {
  it('caps to leave room under the budget', () => {
    expect(fitMaxTokens(10_000, 8192, 12_000)).toBe(12_000 - 10_000 - 256);
  });
  it('returns the request unchanged when it fits or budget is disabled', () => {
    expect(fitMaxTokens(1000, 2048, 12_000)).toBe(2048);
    expect(fitMaxTokens(50_000, 2048, 0)).toBe(2048);
  });
  it('never drops below the floor', () => {
    expect(fitMaxTokens(11_900, 4096, 12_000)).toBe(512);
  });
});

describe('classifyStatus oversize', => {
  it('treats 413 as a rate limit', () => {
    expect(classifyStatus('groq', 413, 'Request too large').kind).toBe('rate_limit');
  });
  it('treats a 400 "tokens per minute" body as a rate limit', () => {
    expect(classifyStatus('groq', 400, 'Limit 12000 tokens per minute, reduce your message').kind).toBe('rate_limit');
  });
  it('keeps a plain 400 transient', () => {
    expect(classifyStatus('groq', 400, 'bad request').kind).toBe('transient');
  });
});
