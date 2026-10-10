import { z } from 'zod';

/** Routing intents (Module 3 §2): recommendation/architecture vs generation/standard. */
export const LlmIntentSchema = z.enum(['recommendation', 'architecture', 'generation', 'standard']);
export type LlmIntent = z.infer<typeof LlmIntentSchema>;

export const LlmProviderIdSchema = z.enum(['bedrock', 'groq', 'gemini', 'grok', 'local', 'mock']);
export type LlmProviderId = z.infer<typeof LlmProviderIdSchema>;

/**
 * Model tier (D-25): the planner/skills select capacity by task weight.
 *   - `local`    → a lightweight locally-hosted model (Ollama/vLLM, OpenAI-compat)
 *                  for light workloads; falls back to mock offline.
 *   - `frontier` → the cost-aware frontier chain (Groq → Gemini → Grok).
 *   - `auto`     → intent-based frontier chain (legacy default).
 * `non_llm` tasks never reach the LLM gateway — the orchestrator runs them
 * deterministically.
 */
export const ModelTierSchema = z.enum(['local', 'frontier', 'auto']);
export type ModelTier = z.infer<typeof ModelTierSchema>;

/**
 * An inline image attached to a chat message (D-66). Base64 payload (no
 * `data:` prefix) plus its MIME type — the gateway renders it into each
 * vision-capable provider's native block shape. Kept small: the orchestrator
 * downscales images before sending, and only vision-capable providers
 * (Bedrock Claude, Gemini) are eligible to serve a request that carries one.
 */
export const ImageBlockSchema = z.object({
  mimeType: z.string().min(1),
  dataBase64: z.string().min(1),
});
export type ImageBlock = z.infer<typeof ImageBlockSchema>;

export const ChatMessageSchema = z.object({
  role: z.enum(['system', 'user', 'assistant']),
  content: z.string(),
  /** Optional inline images (vision). Only honoured by vision-capable providers. */
  images: z.array(ImageBlockSchema).max(8).optional(),
  /**
   * Mark this message as a cacheable prefix (D-98 prompt caching). Honoured by
   * providers that support it (Bedrock Claude: `cache_control: ephemeral`). Use
   * only on STABLE, LEADING content (system prompt / shared context) — the
   * varying per-request instruction must come in a later, uncached message so the
   * cached prefix stays byte-identical across calls (e.g. a per-artifact split).
   */
  cache: z.boolean().optional(),
});
export type ChatMessage = z.infer<typeof ChatMessageSchema>;

/** Request the orchestrator sends to ai-client-service `POST /v1/generate`. */
export const GenerateRequestSchema = z.object({
  intent: LlmIntentSchema,
  messages: z.array(ChatMessageSchema).min(1),
  /** Ask the provider for a strict-JSON response (json mode where supported). */
  json: z.boolean().default(false),
  temperature: z.number().min(0).max(2).default(0.2),
  maxTokens: z.number().int().positive().max(64_000).default(4096),   // providers clamp to their own ceiling (see bedrock.ts)
  /** Free-form tag surfaced in audit logs (e.g. persona or node name). */
  tag: z.string().optional(),
  /** Correlation id from the orchestrator (token governance): echoed in the
   *  gateway's logs and in every attempt record so a ledger row can be matched
   *  to a gateway log line and a provider invocation. */
  requestId: z.string().max(128).optional(),
  /** Model tier to route to (D-25); default auto = intent-based frontier chain. */
  tier: ModelTierSchema.default('auto'),
  /**
   * Pin a specific model (D-68), overriding tier routing. Accepts
   * `"<provider>/<modelId>"` (e.g. `"bedrock/claude-sonnet-5"`) or a bare
   * modelId that matches a configured provider's default. When set, only that
   * provider is tried (then the mock fallback in non-production). Chosen by an
   * authorised user in the Plan Review; unset = normal tier routing.
   */
  model: z.string().optional(),
});
export type GenerateRequest = z.infer<typeof GenerateRequestSchema>;

/**
 * Token usage exactly as the provider reports it (token governance).
 *
 * `promptTokens` is the TOTAL input the model processed (uncached + cache read +
 * cache write), so existing consumers that sum prompt + completion stay correct.
 * The split is carried alongside it: with prompt caching on, Anthropic's own
 * `input_tokens` is only the uncached remainder, and the cached part is reported
 * separately - dropping it under-counts exactly the largest, most repeated calls.
 */
export const LlmUsageSchema = z.object({
  promptTokens: z.number().int().nonnegative(),
  completionTokens: z.number().int().nonnegative(),
  /** Input that was neither read from nor written to the prompt cache. */
  inputTokens: z.number().int().nonnegative().default(0),
  cacheReadTokens: z.number().int().nonnegative().default(0),
  cacheWriteTokens: z.number().int().nonnegative().default(0),
  /** True when the provider reported no usage (e.g. a stream that died) and the
   *  numbers are an estimate from the characters sent and received. */
  estimated: z.boolean().default(false),
});
export type LlmUsage = z.infer<typeof LlmUsageSchema>;

export const AttemptOutcomeSchema = z.enum(['ok', 'error', 'canceled', 'refused']);
export type AttemptOutcome = z.infer<typeof AttemptOutcomeSchema>;

/**
 * One provider attempt (token governance). A logical generate call can make
 * several - retries on the same provider and failover down the chain - and each
 * can have been billed, so the orchestrator writes one ledger row per record.
 */
export const AttemptRecordSchema = z.object({
  provider: LlmProviderIdSchema,
  model: z.string(),
  outcome: AttemptOutcomeSchema,
  /** Failure class (rate_limit | transient | auth_or_billing | canceled | ...). */
  errorKind: z.string().optional(),
  /** Short error text, never request content. */
  error: z.string().max(300).optional(),
  usage: LlmUsageSchema,
  /** The provider's own response/request id (matches Bedrock invocation logs). */
  providerRequestId: z.string().optional(),
  stopReason: z.string().optional(),
  region: z.string().optional(),
  latencyMs: z.number().int().nonnegative().default(0),
});
export type AttemptRecord = z.infer<typeof AttemptRecordSchema>;

export const GenerateResponseSchema = z.object({
  provider: LlmProviderIdSchema,
  model: z.string(),
  content: z.string(),
  usage: LlmUsageSchema,
  /** Providers tried before the one that answered (circuit-breaker visibility). */
  attempts: z.array(z.string()).default([]),
  /** Tier actually served (D-25). */
  tier: ModelTierSchema.default('auto'),
  /** True when the provider stopped at the output-token cap — the content is
   *  incomplete (truncated). Lets the orchestrator retry with a larger budget
   *  instead of failing to parse the cut-off JSON (D-103). */
  truncated: z.boolean().default(false),
  /** Every provider attempt, in order, including failed and retried ones. The
   *  last record with outcome `ok` is the one that answered. */
  usageLog: z.array(AttemptRecordSchema).default([]),
});
export type GenerateResponse = z.infer<typeof GenerateResponseSchema>;

/** Circuit breaker states persisted in Redis (D-04). */
export const BreakerStateSchema = z.enum(['closed', 'open', 'disabled']);
export type BreakerState = z.infer<typeof BreakerStateSchema>;

export const ProviderHealthSchema = z.object({
  provider: LlmProviderIdSchema,
  configured: z.boolean(),
  breaker: BreakerStateSchema,
  /** Seconds until an `open` breaker half-opens; null for closed/disabled. */
  retryInSeconds: z.number().nullable(),
  /** The provider's configured default model id (D-68) — the selectable model. */
  model: z.string().default(''),
  /** Whether the provider accepts inline images (D-66) — surfaced in the catalog. */
  vision: z.boolean().default(false),
});
export type ProviderHealth = z.infer<typeof ProviderHealthSchema>;
