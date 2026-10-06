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

export const LlmUsageSchema = z.object({
  promptTokens: z.number().int().nonnegative(),
  completionTokens: z.number().int().nonnegative(),
});
export type LlmUsage = z.infer<typeof LlmUsageSchema>;

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
