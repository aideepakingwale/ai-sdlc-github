import { pino } from 'pino';
import { describe, expect, it } from 'vitest';
import type { GenerateRequest } from '@sdlc/shared';
import { CircuitBreaker, type BreakerStore } from './breaker.js';
import { createMockProvider } from './providers/mock.js';
import { ProviderCallError, type LlmProvider } from './providers/types.js';
import { chainFor, LlmRouter } from './router.js';

const log = pino({ level: 'silent' });

/** In-memory BreakerStore fake with TTL support. */
function fakeStore(): BreakerStore {
  const data = new Map<string, { value: string; expiresAt: number | null }>();
  return {
    async get(key) {
      const e = data.get(key);
      if (!e) return null;
      if (e.expiresAt !== null && Date.now() > e.expiresAt) {
        data.delete(key);
        return null;
      }
      return e.value;
    },
    async setWithTtl(key, value, ttl) {
      data.set(key, { value, expiresAt: Date.now() + ttl * 1000 });
    },
    async setPersistent(key, value) {
      data.set(key, { value, expiresAt: null });
    },
    async ttl(key) {
      const e = data.get(key);
      if (!e || e.expiresAt === null) return -1;
      return Math.ceil((e.expiresAt - Date.now()) / 1000);
    },
  };
}

function scripted(id: 'groq' | 'gemini' | 'grok', behaviour: () => Promise<string>): LlmProvider {
  return {
    id,
    configured: true,
    model: `${id}-test`,
    async generate() {
      const content = await behaviour();
      return { content, model: `${id}-test`, usage: { promptTokens: 1, completionTokens: 1 } };
    },
  };
}

const req: GenerateRequest = {
  intent: 'generation',
  messages: [{ role: 'user', content: 'hello' }],
  json: false,
  temperature: 0.2,
  maxTokens: 128,
  tier: 'auto',
};

describe('chainFor', () => {
  it('bedrock leads every frontier chain; key-based providers follow', => {
    expect(chainFor('architecture')).toEqual(['bedrock', 'groq', 'grok', 'gemini']);
    expect(chainFor('generation')).toEqual(['bedrock', 'groq', 'gemini', 'grok']);
  });
});

describe('bedrock routing', => {
  it('serves from bedrock first when configured', async () => {
    const breaker = new CircuitBreaker(fakeStore(), 0);
    const bedrock: LlmProvider = {
      id: 'bedrock',
      configured: true,
      model: 'anthropic.claude-opus-4-8',
      async generate() {
        return {
          content: 'from-bedrock',
          model: 'anthropic.claude-opus-4-8',
          usage: { promptTokens: 1, completionTokens: 1 },
        };
      },
    };
    const groq = scripted('groq', async () => 'from-groq');
    const router = new LlmRouter([bedrock, groq], breaker, log, {
      forceMock: false,
      allowMockFallback: false,
    });
    const res = await router.generate(req, new AbortController().signal);
    expect(res.provider).toBe('bedrock');
    expect(res.model).toBe('anthropic.claude-opus-4-8');
  });

  it('fails over from bedrock to the key-based chain on transient errors', async () => {
    const breaker = new CircuitBreaker(fakeStore(), 0);
    const bedrock: LlmProvider = {
      id: 'bedrock',
      configured: true,
      model: 'anthropic.claude-opus-4-8',
      async generate() {
        throw new ProviderCallError('bedrock', 'transient', 'throttled', 500);
      },
    };
    const groq = scripted('groq', async () => 'from-groq');
    const router = new LlmRouter([bedrock, groq], breaker, log, {
      forceMock: false,
      allowMockFallback: false,
    });
    const res = await router.generate(req, new AbortController().signal);
    expect(res.provider).toBe('groq');
    expect(res.attempts).toContain('bedrock:transient');
    expect(await breaker.state('bedrock')).toBe('open');
  });

  it('unconfigured bedrock is skipped without affecting the chain', async () => {
    const breaker = new CircuitBreaker(fakeStore(), 0);
    const bedrock: LlmProvider = {
      id: 'bedrock',
      configured: false,
      model: 'anthropic.claude-opus-4-8',
      async generate() {
        throw new Error('should never be called');
      },
    };
    const groq = scripted('groq', async () => 'from-groq');
    const router = new LlmRouter([bedrock, groq], breaker, log, {
      forceMock: false,
      allowMockFallback: false,
    });
    const res = await router.generate(req, new AbortController().signal);
    expect(res.provider).toBe('groq');
  });
});

describe('local model as real fallback', => {
  it('serves the frontier chain from the local model when no cloud provider is configured', async () => {
    const breaker = new CircuitBreaker(fakeStore(), 0);
    const local: LlmProvider = {
      id: 'local',
      configured: true,
      model: 'qwen-local',
      async generate() {
        return { content: 'from-local', model: 'qwen-local', usage: { promptTokens: 1, completionTokens: 1 } };
      },
    };
    // No cloud providers registered; local backs the frontier tier before mock.
    const router = new LlmRouter([local, createMockProvider()], breaker, log, {
      forceMock: false,
      allowMockFallback: false,
    });
    const res = await router.generate(req, new AbortController().signal); // req.tier = 'auto'
    expect(res.provider).toBe('local');
  });

  it('prefers a configured cloud provider over the local fallback', async () => {
    const breaker = new CircuitBreaker(fakeStore(), 0);
    const local: LlmProvider = {
      id: 'local', configured: true, model: 'qwen-local',
      async generate() {
        return { content: 'local', model: 'qwen-local', usage: { promptTokens: 1, completionTokens: 1 } };
      },
    };
    const groq = scripted('groq', async () => 'from-groq');
    const router = new LlmRouter([groq, local], breaker, log, {
      forceMock: false, allowMockFallback: false,
    });
    const res = await router.generate(req, new AbortController().signal);
    expect(res.provider).toBe('groq');
  });
});

describe('LlmRouter failover', () => {
  it('fails over to the next provider on auth error and disables the first permanently', async () => {
    const breaker = new CircuitBreaker(fakeStore(), 0);
    let groqCalls = 0;
    const groq = scripted('groq', async () => {
      groqCalls++;
      throw new ProviderCallError('groq', 'auth_or_billing', '401', 401);
    });
    const gemini = scripted('gemini', async () => 'from-gemini');
    const router = new LlmRouter([groq, gemini], breaker, log, {
      forceMock: false,
      allowMockFallback: false,
    });

    const res1 = await router.generate(req, new AbortController().signal);
    expect(res1.provider).toBe('gemini');
    expect(res1.attempts).toContain('groq:auth_or_billing');
    expect(await breaker.state('groq')).toBe('disabled');

    // Second call must skip groq entirely (DISABLED_FOR_LIFETIME semantics).
    const res2 = await router.generate(req, new AbortController().signal);
    expect(res2.provider).toBe('gemini');
    expect(groqCalls).toBe(1);
  });

  it('retries 429 with backoff then trips the breaker open', async () => {
    const breaker = new CircuitBreaker(fakeStore(), 0);
    let calls = 0;
    const groq = scripted('groq', async () => {
      calls++;
      throw new ProviderCallError('groq', 'rate_limit', '429', 429);
    });
    const gemini = scripted('gemini', async () => 'ok');
    const router = new LlmRouter([groq, gemini], breaker, log, {
      forceMock: false,
      allowMockFallback: false,
    });

    const res = await router.generate(req, new AbortController().signal);
    expect(res.provider).toBe('gemini');
    expect(calls).toBe(4); // initial + 3 backoff retries
    expect(await breaker.state('groq')).toBe('open');
  }, 15_000);

  it('throws PROVIDER_EXHAUSTED when every provider fails and no mock fallback', async () => {
    const breaker = new CircuitBreaker(fakeStore(), 0);
    const bad = (id: 'groq' | 'gemini' | 'grok') =>
      scripted(id, async () => {
        throw new ProviderCallError(id, 'transient', 'boom', 500);
      });
    const router = new LlmRouter([bad('groq'), bad('gemini'), bad('grok')], breaker, log, {
      forceMock: false,
      allowMockFallback: false,
    });
    await expect(router.generate(req, new AbortController().signal)).rejects.toMatchObject({
      code: 'PROVIDER_EXHAUSTED',
    });
  });

  it('falls back to mock outside production', async () => {
    const breaker = new CircuitBreaker(fakeStore(), 0);
    const groq = scripted('groq', async () => {
      throw new ProviderCallError('groq', 'transient', 'down', 503);
    });
    const router = new LlmRouter([groq, createMockProvider()], breaker, log, {
      forceMock: false,
      allowMockFallback: true,
    });
    const res = await router.generate(req, new AbortController().signal);
    expect(res.provider).toBe('mock');
  });

  it('tier=local routes to the local provider and reports the tier', async => {
    const breaker = new CircuitBreaker(fakeStore(), 0);
    const local: LlmProvider = {
      id: 'local',
      configured: true,
      model: 'qwen-local',
      async generate() {
        return { content: 'from-local', model: 'qwen-local', usage: { promptTokens: 1, completionTokens: 1 } };
      },
    };
    const groq = scripted('groq', async () => 'from-groq');
    const router = new LlmRouter([groq, local, createMockProvider()], breaker, log, {
      forceMock: false,
      allowMockFallback: false,
    });
    const res = await router.generate({ ...req, tier: 'local' }, new AbortController().signal);
    expect(res.provider).toBe('local');
    expect(res.tier).toBe('local');
  });

  it('tier=local falls back to mock when no local provider is configured', async () => {
    const breaker = new CircuitBreaker(fakeStore(), 0);
    const groq = scripted('groq', async () => 'from-groq');
    const router = new LlmRouter([groq, createMockProvider()], breaker, log, {
      forceMock: false,
      allowMockFallback: true,
    });
    const res = await router.generate({ ...req, tier: 'local' }, new AbortController().signal);
    expect(res.provider).toBe('mock');
    expect(res.tier).toBe('local');
  });
});

describe('vision routing', => {
  const visionProvider = (id: 'bedrock' | 'gemini', content: string): LlmProvider => ({
    id,
    configured: true,
    model: `${id}-vision`,
    vision: true,
    async generate() {
      return { content, model: `${id}-vision`, usage: { promptTokens: 1, completionTokens: 1 } };
    },
  });
  const imageReq: GenerateRequest = {
    ...req,
    intent: 'generation',
    messages: [
      { role: 'user', content: 'describe this', images: [{ mimeType: 'image/jpeg', dataBase64: 'AAAA' }] },
    ],
  };

  it('routes an image request only to vision-capable providers (multi-model)', async () => {
    const breaker = new CircuitBreaker(fakeStore(), 0);
    // groq is text-only and earlier in the failover order, but must be skipped.
    const groq = scripted('groq', async () => {
      throw new Error('text-only provider must not receive an image request');
    });
    const bedrock = visionProvider('bedrock', 'from-bedrock-vision');
    const router = new LlmRouter([bedrock, groq], breaker, log, {
      forceMock: false,
      allowMockFallback: false,
    });
    const res = await router.generate(imageReq, new AbortController().signal);
    expect(res.provider).toBe('bedrock');
  });

  it('fails over across vision providers (bedrock -> gemini)', async () => {
    const breaker = new CircuitBreaker(fakeStore(), 0);
    const bedrock: LlmProvider = {
      id: 'bedrock', configured: true, model: 'bedrock-vision', vision: true,
      async generate() {
        throw new ProviderCallError('bedrock', 'transient', 'throttled', 500);
      },
    };
    const gemini = visionProvider('gemini', 'from-gemini-vision');
    const router = new LlmRouter([bedrock, gemini], breaker, log, {
      forceMock: false, allowMockFallback: false,
    });
    const res = await router.generate(imageReq, new AbortController().signal);
    expect(res.provider).toBe('gemini');
    expect(res.attempts).toContain('bedrock:transient');
  });

  it('the mock is vision-capable so offline image requests still resolve', async () => {
    const breaker = new CircuitBreaker(fakeStore(), 0);
    const groq = scripted('groq', async () => 'text-only'); // excluded (no vision)
    const router = new LlmRouter([groq, createMockProvider()], breaker, log, {
      forceMock: false, allowMockFallback: true,
    });
    const res = await router.generate(imageReq, new AbortController().signal);
    expect(res.provider).toBe('mock');
    expect(res.content).toContain('[mock-vision]');
  });
});

describe('model override', => {
  const capturing = (id: 'bedrock' | 'gemini', model: string) => {
    let last: GenerateRequest | null = null;
    const p: LlmProvider = {
      id, configured: true, model,
      async generate(r) { last = r; return { content: `from-${id}`, model: r.model || model, usage: { promptTokens: 1, completionTokens: 1 } }; },
    };
    return { p, seen: () => last };
  };

  it('pins to the requested provider/model and hands the provider the bare model id', async () => {
    const breaker = new CircuitBreaker(fakeStore(), 0);
    const bd = capturing('bedrock', 'claude-opus-4-8');
    const gm = capturing('gemini', 'gemini-2.5-flash');
    const router = new LlmRouter([bd.p, gm.p], breaker, log, { forceMock: false, allowMockFallback: false });
    const res = await router.generate({ ...req, model: 'bedrock/claude-sonnet-5' }, new AbortController().signal);
    expect(res.provider).toBe('bedrock');
    expect(res.model).toBe('claude-sonnet-5');        // provider used the overridden model
    expect(bd.seen()?.model).toBe('claude-sonnet-5'); // prefix stripped to a bare id
    expect(gm.seen()).toBeNull();                      // other providers never tried
  });

  it('a bare model id routes to the provider whose default matches', async () => {
    const breaker = new CircuitBreaker(fakeStore(), 0);
    const gm = capturing('gemini', 'gemini-2.5-flash');
    const router = new LlmRouter([gm.p, createMockProvider()], breaker, log, { forceMock: false, allowMockFallback: true });
    const res = await router.generate({ ...req, model: 'gemini-2.5-flash' }, new AbortController().signal);
    expect(res.provider).toBe('gemini');
  });

  it('an unrecognised override is ignored — normal tier routing still serves', async () => {
    const breaker = new CircuitBreaker(fakeStore(), 0);
    const gm = capturing('gemini', 'gemini-2.5-flash');
    const router = new LlmRouter([gm.p, createMockProvider()], breaker, log, { forceMock: false, allowMockFallback: true });
    const res = await router.generate({ ...req, model: 'does-not-exist' }, new AbortController().signal);
    expect(res.provider).toBe('gemini'); // falls through to the intent chain, never dead-ends
  });

  it('health() exposes each provider model + vision for the catalog', async () => {
    const breaker = new CircuitBreaker(fakeStore(), 0);
    const gm = capturing('gemini', 'gemini-2.5-flash');
    const router = new LlmRouter([gm.p, createMockProvider()], breaker, log, { forceMock: false, allowMockFallback: true });
    const h = await router.health();
    const g = h.find((x) => x.provider === 'gemini');
    expect(g?.model).toBe('gemini-2.5-flash');
    expect(typeof g?.vision).toBe('boolean');
  });
});

describe('mock corpus diagrams are valid mermaid', => {
  const render = async (kind: string, field: string): Promise<string> => {
    const mock = createMockProvider();
    const res = await mock.generate(
      { ...req, messages: [
        { role: 'system' as const, content: `#mock:${kind}` },
        { role: 'user' as const, content: 'Build a settlement reconciliation service' },
      ] },
      new AbortController().signal,
    );
    const parsed = JSON.parse(res.content) as Record<string, unknown>;
    const value = parsed[field];
    expect(typeof value).toBe('string');
    return (value as string).trim();
  };

  it('architecture diagram starts with a diagram-type keyword', async () => {
    expect(await render('phase2', 'mermaidArchitecture')).toMatch(/^(flowchart|graph)\b/);
  });

  it('sequence diagram has no ";" in message text (mermaid treats it as a separator)', async () => {
    const seq = await render('phase3', 'mermaidSequence');
    expect(seq).toMatch(/^sequenceDiagram\b/);
    const offending = seq.split('\n').filter((l) => /(->>|-->>)/.test(l) && l.includes(';'));
    expect(offending).toEqual([]);
  });
});

describe('mock provider directives', () => {
  it('returns schema-valid JSON per #mock directive and is deterministic', async () => {
    const mock = createMockProvider();
    const r = { ...req, messages: [{ role: 'system' as const, content: '#mock:phase1' }, { role: 'user' as const, content: 'Build a loyalty points API' }] };
    const a = await mock.generate(r, new AbortController().signal);
    const b = await mock.generate(r, new AbortController().signal);
    expect(a.content).toBe(b.content);
    const parsed = JSON.parse(a.content) as {
      epics: Array<{ businessCase: string; features: Array<{ stories: unknown[] }> }>;
      prdMarkdown: string;
      definitionOfReady: string[];
    };
    expect(parsed.epics.length).toBeGreaterThan(0);
    expect(parsed.prdMarkdown).toContain('Product Requirements Document');
    // the corpus must be professional-grade, not placeholder text.
    expect(parsed.prdMarkdown).toContain('Non-functional requirements');
    // a real Agile hierarchy — Epic -> Feature -> Story with structured
    // statement, Gherkin ACs and sub-tasks, plus a Definition of Ready.
    expect(parsed.epics[0]!.businessCase).toBeTruthy();
    expect(parsed.definitionOfReady.length).toBeGreaterThan(0);
    const firstStory = parsed.epics[0]!.features[0]!.stories[0] as {
      statement: { asA: string; iWant: string; soThat: string };
      acceptanceCriteria: string[];
      subTasks: unknown[];
    };
    expect(firstStory.statement.asA).toBeTruthy();
    expect(firstStory.acceptanceCriteria.length).toBeGreaterThanOrEqual(3);
    expect(firstStory.subTasks.length).toBeGreaterThan(0);
  });
});
