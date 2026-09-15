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

// Which real providers have credentials — the multimodel roster.
const activeProviders = [
  env.BEDROCK_MODEL_ID ? 'bedrock' : null,
  env.GROQ_API_KEY ? 'groq' : null,
  env.GEMINI_API_KEY ? 'gemini' : null,
  env.XAI_API_KEY ? 'grok' : null,
  env.LOCAL_LLM_BASE_URL ? 'local' : null,
].filter((p): p is string => p !== null);

// Resolve the effective mode: GENERATION_MODE is the master switch; the legacy
// LLM_FORCE_MOCK still pins mock for back-compat.
const mode: 'mock' | 'llm' | 'auto' = env.LLM_FORCE_MOCK ? 'mock' : env.GENERATION_MODE;

if (mode === 'llm' && activeProviders.length === 0) {
  log.error(
    'GENERATION_MODE=llm but no LLM provider is configured. Set BEDROCK_MODEL_ID, GROQ_API_KEY, ' +
      'GEMINI_API_KEY, XAI_API_KEY or LOCAL_LLM_BASE_URL — or use GENERATION_MODE=auto/mock.',
  );
  process.exit(1);
}

const effectiveMock = mode === 'mock' || (mode === 'auto' && activeProviders.length === 0);
log.info({ mode, effectiveMock, activeProviders }, 'generation mode resolved');

const router = new LlmRouter(
  [
    createBedrockProvider({
      modelId: env.BEDROCK_MODEL_ID,
      region: env.BEDROCK_REGION ?? process.env.AWS_REGION,
    }),
    createOpenAiCompatProvider({
      id: 'groq',
      baseUrl: 'https://api.groq.com/openai/v1',
      apiKey: env.GROQ_API_KEY,
      model: env.GROQ_MODEL,
      requestTokenBudget: env.LLM_REQUEST_TOKEN_BUDGET,
    }),
    createGeminiProvider({ apiKey: env.GEMINI_API_KEY, model: env.GEMINI_MODEL }),
    createOpenAiCompatProvider({
      id: 'grok',
      baseUrl: 'https://api.x.ai/v1',
      apiKey: env.XAI_API_KEY,
      model: env.XAI_MODEL,
      requestTokenBudget: env.LLM_REQUEST_TOKEN_BUDGET,
    }),
    createOpenAiCompatProvider({
      id: 'local',
      baseUrl: (env.LOCAL_LLM_BASE_URL ?? 'http://local-llm:11434/v1').replace(/\/$/, ''),
      apiKey: env.LOCAL_LLM_API_KEY,
      model: env.LOCAL_LLM_MODEL,
      configuredWhen: Boolean(env.LOCAL_LLM_BASE_URL),
      timeoutMs: 90_000,
    }),
    createMockProvider(),
  ],
  breaker,
  log,
  {
    forceMock: effectiveMock,
    // In explicit `llm` mode the mock is never a silent fallback — a real
    // provider must serve or the request fails. Otherwise mock backstops
    // non-production so local runs never dead-end.
    allowMockFallback: mode !== 'llm' && env.NODE_ENV !== 'production',
  },
);

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

app.get('/v1/providers', async () => ({
  mode, // mock | llm | auto (requested)
  effectiveMock, // true = the deterministic mock is serving generation
  activeProviders, // real providers with credentials (multimodel roster)
  providers: await router.health(),
}));

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
    const res = await router.generate(parsed.data, controller.signal);
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
