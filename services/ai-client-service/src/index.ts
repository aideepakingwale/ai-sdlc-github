import Fastify from 'fastify';
import { Redis } from 'ioredis';
import { pino } from 'pino';
import {
  AiClientEnvSchema,
  GenerateRequestSchema,
  isSdlcError,
  loadEnv,
} from '@sdlc/shared';
import { CircuitBreaker, redisBreakerStore } from './breaker.js';
import { createBedrockProvider } from './providers/bedrock.js';
import { createGeminiProvider } from './providers/gemini.js';
import { createMockProvider } from './providers/mock.js';
import { createOpenAiCompatProvider } from './providers/openai-compat.js';
import { LlmRouter } from './router.js';

const env = loadEnv(AiClientEnvSchema);
const log = pino({ level: env.LOG_LEVEL, name: 'ai-client' });

const redis = new Redis(env.REDIS_URL, { maxRetriesPerRequest: 2 });
const breaker = new CircuitBreaker(redisBreakerStore(redis));

// D-91/D-92: LLM config is resolved PER REQUEST from Super-Admin runtime overrides
// (Redis, written by the orchestrator from durable platform_settings rows), falling
// back to env per field. The router + provider roster are rebuilt live when the
// effective provider config changes — so region/model/keys/model-names all apply
// with no restart. `optsFor(mode)` supplies mock/fallback behaviour per request.
type GenMode = 'mock' | 'llm' | 'auto';
const K = (name: string) => `sdlc:settings:${name}`;
const SETTING_KEYS = [
  'generation_mode', 'bedrock_model_id', 'bedrock_region',
  'groq_api_key', 'groq_model', 'gemini_api_key', 'gemini_model', 'xai_api_key', 'xai_model',
] as const;

interface EffCfg {
  mode: GenMode;
  bedrockModelId?: string;
  bedrockRegion?: string;
  groqApiKey?: string; groqModel: string;
  geminiApiKey?: string; geminiModel: string;
  xaiApiKey?: string; xaiModel: string;
}

function cfgFromEnv(): EffCfg {
  return {
    mode: env.LLM_FORCE_MOCK ? 'mock' : env.GENERATION_MODE,
    bedrockModelId: env.BEDROCK_MODEL_ID,
    bedrockRegion: env.BEDROCK_REGION ?? process.env.AWS_REGION,
    groqApiKey: env.GROQ_API_KEY, groqModel: env.GROQ_MODEL,
    geminiApiKey: env.GEMINI_API_KEY, geminiModel: env.GEMINI_MODEL,
    xaiApiKey: env.XAI_API_KEY, xaiModel: env.XAI_MODEL,
  };
}

async function readEffectiveConfig(): Promise<EffCfg> {
  if (env.LLM_FORCE_MOCK) return { ...cfgFromEnv(), mode: 'mock' };
  let ov: (string | null)[] = new Array(SETTING_KEYS.length).fill(null);
  try {
    ov = await redis.mget(...SETTING_KEYS.map(K));
  } catch {
    return cfgFromEnv(); // Redis down => env only
  }
  const o = Object.fromEntries(SETTING_KEYS.map((k, i) => [k, ov[i] || undefined])) as Record<string, string | undefined>;
  const m = o.generation_mode;
  return {
    mode: m === 'mock' || m === 'llm' || m === 'auto' ? m : env.GENERATION_MODE,
    bedrockModelId: o.bedrock_model_id ?? env.BEDROCK_MODEL_ID,
    bedrockRegion: o.bedrock_region ?? env.BEDROCK_REGION ?? process.env.AWS_REGION,
    groqApiKey: o.groq_api_key ?? env.GROQ_API_KEY,
    groqModel: o.groq_model ?? env.GROQ_MODEL,
    geminiApiKey: o.gemini_api_key ?? env.GEMINI_API_KEY,
    geminiModel: o.gemini_model ?? env.GEMINI_MODEL,
    xaiApiKey: o.xai_api_key ?? env.XAI_API_KEY,
    xaiModel: o.xai_model ?? env.XAI_MODEL,
  };
}

function activeProvidersFor(cfg: EffCfg): string[] {
  return [
    cfg.bedrockModelId ? 'bedrock' : null,
    cfg.groqApiKey ? 'groq' : null,
    cfg.geminiApiKey ? 'gemini' : null,
    cfg.xaiApiKey ? 'grok' : null,
    env.LOCAL_LLM_BASE_URL ? 'local' : null,
  ].filter((p): p is string => p !== null);
}

function buildRouter(cfg: EffCfg): LlmRouter {
  return new LlmRouter(
    [
      // D-102: background workers generate with no live request waiting, so give
      // each provider a generous streaming ceiling (LLM_STREAM_TIMEOUT_MS, 1h) —
      // a large artifact must not be aborted mid-stream.
      createBedrockProvider({
        modelId: cfg.bedrockModelId, region: cfg.bedrockRegion, timeoutMs: env.LLM_STREAM_TIMEOUT_MS,
      }),
      createOpenAiCompatProvider({
        id: 'groq', baseUrl: 'https://api.groq.com/openai/v1',
        apiKey: cfg.groqApiKey, model: cfg.groqModel, requestTokenBudget: env.LLM_REQUEST_TOKEN_BUDGET,
        timeoutMs: env.LLM_STREAM_TIMEOUT_MS,
      }),
      createGeminiProvider({
        apiKey: cfg.geminiApiKey, model: cfg.geminiModel, timeoutMs: env.LLM_STREAM_TIMEOUT_MS,
      }),
      createOpenAiCompatProvider({
        id: 'grok', baseUrl: 'https://api.x.ai/v1',
        apiKey: cfg.xaiApiKey, model: cfg.xaiModel, requestTokenBudget: env.LLM_REQUEST_TOKEN_BUDGET,
        timeoutMs: env.LLM_STREAM_TIMEOUT_MS,
      }),
      createOpenAiCompatProvider({
        id: 'local',
        baseUrl: (env.LOCAL_LLM_BASE_URL ?? 'http://local-llm:11434/v1').replace(/\/$/, ''),
        apiKey: env.LOCAL_LLM_API_KEY, model: env.LOCAL_LLM_MODEL,
        configuredWhen: Boolean(env.LOCAL_LLM_BASE_URL), timeoutMs: 90_000,
      }),
      createMockProvider(),
    ],
    breaker,
    log,
    // Per-request opts (below) are the source of truth; these defaults are unused.
    { forceMock: false, allowMockFallback: true },
  );
}

function optsFor(mode: GenMode, active: string[]): { forceMock: boolean; allowMockFallback: boolean } {
  return {
    forceMock: mode === 'mock' || (mode === 'auto' && active.length === 0),
    allowMockFallback: mode !== 'llm' && env.NODE_ENV !== 'production',
  };
}

// Provider signature excludes `mode` (mode is applied via optsFor, not baked into
// the router) so a mode toggle doesn't force a router rebuild.
function providerSig(cfg: EffCfg): string {
  const { mode: _mode, ...rest } = cfg;
  return JSON.stringify(rest);
}

let current: { psig: string; router: LlmRouter; active: string[]; mode: GenMode };
{
  const cfg = cfgFromEnv();
  current = { psig: providerSig(cfg), router: buildRouter(cfg), active: activeProvidersFor(cfg), mode: cfg.mode };
  log.info({ mode: current.mode, activeProviders: current.active }, 'generation mode resolved');
  if (current.mode === 'llm' && current.active.length === 0) {
    log.warn('GENERATION_MODE=llm but no provider configured yet — set one in the admin panel or .env.');
  }
}

async function ensureCurrent(): Promise<typeof current> {
  const cfg = await readEffectiveConfig();
  const psig = providerSig(cfg);
  if (psig !== current.psig) {
    current = { psig, router: buildRouter(cfg), active: activeProvidersFor(cfg), mode: cfg.mode };
    // D-94: a config change gives every provider a fresh chance — clear any breaker
    // a prior misconfig tripped (e.g. an auth 403 that 'disabled' a provider), so an
    // admin fix in the UI self-heals without a manual Redis reset.
    await Promise.all(
      ['bedrock', 'groq', 'gemini', 'grok', 'local'].map((p) => breaker.reset(p).catch(() => {})),
    );
    log.info({ mode: cfg.mode, activeProviders: current.active }, 'llm config reloaded — breakers reset');
  } else {
    current.mode = cfg.mode;
  }
  return current;
}

const app = Fastify({ logger: false, bodyLimit: 4 * 1024 * 1024 });

app.get('/healthz', async () => ({ status: 'ok' }));

app.get('/readyz', async (_req, reply) => {
  try {
    await redis.ping();
    return { status: 'ok' };
  } catch {
    return reply.status(503).send({ status: 'degraded', redis: 'down' });
  }
});

app.get('/v1/providers', async () => {
  const c = await ensureCurrent(); // env default OR Super-Admin overrides (live)
  return {
    mode: c.mode, // mock | llm | auto (effective)
    effectiveMock: optsFor(c.mode, c.active).forceMock, // true = the mock is serving
    activeProviders: c.active, // real providers with credentials (multimodel roster)
    providers: await c.router.health(),
  };
});

app.post('/v1/generate', async (req, reply) => {
  const parsed = GenerateRequestSchema.safeParse(req.body);
  if (!parsed.success) {
    return reply.status(400).send({
      error: { code: 'VALIDATION_FAILED', message: parsed.error.issues.map((i) => i.message).join('; ') },
    });
  }
  // Cancel the upstream LLM call ONLY when the client actually disconnects.
  // `req.raw`'s 'close' fires on Node 18+ when the request BODY is consumed —
  // aborting there kills every in-flight provider call and silently forces the
  // mock fallback (the root cause of "everything runs as mock" even with valid
  // keys). Bind to `reply.raw` and abort only if the response never finished.
  const controller = new AbortController();
  reply.raw.on('close', () => {
    if (!reply.raw.writableFinished) controller.abort();
  });
  try {
    const started = Date.now();
    // D-91/D-92: resolve effective config per request (rebuilds router live if it changed).
    const c = await ensureCurrent();
    const res = await c.router.generate(parsed.data, controller.signal, optsFor(c.mode, c.active));
    log.info(
      {
        provider: res.provider,
        model: res.model,
        intent: parsed.data.intent,
        tag: parsed.data.tag,
        ms: Date.now() - started,
        usage: res.usage,
      },
      'generate ok',
    );
    return res;
  } catch (err) {
    if (isSdlcError(err)) {
      return reply.status(err.httpStatus).send({ error: { code: err.code, message: err.message, details: err.details } });
    }
    log.error({ err }, 'generate failed');
    return reply.status(500).send({ error: { code: 'INTERNAL', message: 'Internal error' } });
  }
});

async function main() {
  await app.listen({ port: env.AI_CLIENT_PORT, host: '0.0.0.0' });
  log.info({ port: env.AI_CLIENT_PORT }, 'ai-client-service listening');
}

async function shutdown(signal: string) {
  log.info({ signal }, 'shutting down');
  await app.close();
  redis.disconnect();
  process.exit(0);
}

process.on('SIGTERM', () => void shutdown('SIGTERM'));
process.on('SIGINT', () => void shutdown('SIGINT'));

main().catch((err) => {
  log.error({ err }, 'fatal boot error');
  process.exit(1);
});
