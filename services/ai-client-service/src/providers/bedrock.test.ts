import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { GenerateRequest } from '@sdlc/shared';
import { ProviderCallError } from './types.js';

/**
 * Token governance: the Bedrock adapter must report every token the provider
 * bills - cache reads and writes included - and must keep what it knows when a
 * call fails after generation began. The SDK is faked at its module boundary.
 */
type FakeMsg = {
  id?: string; model?: string; stop_reason?: string | null;
  content: Array<{ type: string; text?: string }>;
  usage: { input_tokens: number; output_tokens: number; cache_read_input_tokens?: number | null; cache_creation_input_tokens?: number | null };
};
const script: {
  final?: FakeMsg; snapshot?: FakeMsg; deltas?: string[]; fail?: unknown; headerId?: string | null;
} = {};

vi.mock('@anthropic-ai/bedrock-sdk', () => ({
  AnthropicBedrock: class {
    messages = {
      stream: () => {
        const handlers: Array<(d: string) => void> = [];
        return {
          currentMessage: script.snapshot,
          request_id: null,
          response: script.headerId === undefined ? null : { headers: { get: (k: string) => (k === 'x-amzn-requestid' ? script.headerId : null) } },
          on(ev: string, cb: (d: string) => void) { if (ev === 'text') handlers.push(cb); },
          async finalMessage() {
            for (const d of script.deltas ?? []) handlers.forEach((h) => h(d));
            if (script.fail) throw script.fail;
            return script.final;
          },
        };
      },
    };
  },
}));

const { createBedrockProvider } = await import('./bedrock.js');

const req: GenerateRequest = {
  intent: 'generation', messages: [{ role: 'system', content: 'sys', cache: true }, { role: 'user', content: 'hello' }],
  json: false, temperature: 0.2, maxTokens: 1024, tier: 'auto',
};
const provider = () => createBedrockProvider({ modelId: 'anthropic.claude-test', region: 'eu-west-1' });
const run = () => provider().generate(req, new AbortController().signal);

beforeEach(() => { for (const k of Object.keys(script)) delete (script as Record<string, unknown>)[k]; });

describe('bedrock usage capture', () => {
  it('adds cache reads and writes back to the input total and keeps the split', async () => {
    script.headerId = 'req-123';
    script.final = {
      id: 'msg_1', model: 'anthropic.claude-test', stop_reason: 'end_turn',
      content: [{ type: 'text', text: 'ok' }],
      usage: { input_tokens: 10, output_tokens: 300, cache_read_input_tokens: 5000, cache_creation_input_tokens: 200 },
    };
    const r = await run();
    expect(r.usage).toEqual({
      promptTokens: 5210, completionTokens: 300, inputTokens: 10, cacheReadTokens: 5000, cacheWriteTokens: 200, estimated: false,
    });
    expect(r.providerRequestId).toBe('req-123');
    expect(r.stopReason).toBe('end_turn');
    expect(r.region).toBe('eu-west-1');
    expect(r.truncated).toBe(false);
  });

  it('treats null cache counters as zero and falls back to the message id', async () => {
    script.final = {
      id: 'msg_9', stop_reason: 'max_tokens', content: [{ type: 'text', text: 'cut' }],
      usage: { input_tokens: 7, output_tokens: 3, cache_read_input_tokens: null, cache_creation_input_tokens: null },
    };
    const r = await run();
    expect(r.usage.promptTokens).toBe(7);
    expect(r.providerRequestId).toBe('msg_9');
    expect(r.truncated).toBe(true);
  });

  it('a refusal is billed: the error carries the real usage', async () => {
    script.final = {
      id: 'msg_r', stop_reason: 'refusal', content: [],
      usage: { input_tokens: 40, output_tokens: 5, cache_read_input_tokens: 100 },
    };
    const err = await run().catch((e) => e);
    expect(err).toBeInstanceOf(ProviderCallError);
    expect((err as ProviderCallError).info.usage).toMatchObject({ promptTokens: 140, completionTokens: 5, cacheReadTokens: 100, estimated: false });
    expect((err as ProviderCallError).info.stopReason).toBe('refusal');
  });

  it('a mid-stream failure keeps the input the stream reported and estimates the output', async () => {
    script.snapshot = {
      content: [], usage: { input_tokens: 100, output_tokens: 1, cache_read_input_tokens: 900, cache_creation_input_tokens: 0 },
    };
    script.deltas = ['x'.repeat(400)];
    script.fail = Object.assign(new Error('overloaded'), { status: 529 });
    const err = (await run().catch((e) => e)) as ProviderCallError;
    expect(err).toBeInstanceOf(ProviderCallError);
    expect(err.kind).toBe('transient');
    expect(err.info.usage).toMatchObject({ inputTokens: 100, cacheReadTokens: 900, promptTokens: 1000, completionTokens: 100, estimated: true });
  });

  it('streamed text with no usage snapshot is estimated from characters', async () => {
    script.deltas = ['y'.repeat(80)];
    script.fail = new Error('socket hang up');
    const err = (await run().catch((e) => e)) as ProviderCallError;
    expect(err.info.usage?.estimated).toBe(true);
    expect(err.info.usage?.completionTokens).toBe(20);
    expect(err.info.usage?.promptTokens).toBeGreaterThan(0);
  });

  it('a call that never started generating bills nothing', async () => {
    script.fail = Object.assign(new Error('throttled'), { status: 429 });
    const err = (await run().catch((e) => e)) as ProviderCallError;
    expect(err.kind).toBe('rate_limit');
    expect(err.info.usage).toBeUndefined();
  });

  it('an abort is canceled, not a provider fault, and keeps partial usage', async () => {
    script.snapshot = { content: [], usage: { input_tokens: 50, output_tokens: 1 } };
    script.deltas = ['z'.repeat(40)];
    script.fail = Object.assign(new Error('Request was aborted'), { name: 'APIUserAbortError' });
    const err = (await run().catch((e) => e)) as ProviderCallError;
    expect(err.kind).toBe('canceled');
    expect(err.info.usage?.inputTokens).toBe(50);
    expect(err.info.usage?.completionTokens).toBe(10);
  });
});
