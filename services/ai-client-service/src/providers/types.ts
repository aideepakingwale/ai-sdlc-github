import type { GenerateRequest, LlmProviderId, LlmUsage } from '@sdlc/shared';

export interface ProviderResult {
  content: string;
  model: string;
  usage: LlmUsage;
  /** True when the provider stopped at the output-token cap (max_tokens /
   *  length / MAX_TOKENS) — the content is incomplete (D-103). */
  truncated?: boolean;
}

/**
 * Provider failure classification drives the circuit breaker (Module 3 §2):
 * - `rate_limit` (429): exponential backoff retries, then trip breaker `open` (TTL)
 * - `auth_or_billing` (401/402/403): breaker `disabled` permanently (D-04)
 * - `transient` (5xx / network): one retry, then trip `open`
 * - `canceled` (D-103): the request was aborted — caller disconnected or our own
 *   timeout fired. NOT a provider fault: no retry, and the breaker is NOT tripped
 *   (a client disconnect must never disable the provider for everyone else).
 */
export type ProviderFailureKind = 'rate_limit' | 'auth_or_billing' | 'transient' | 'canceled';

export class ProviderCallError extends Error {
  constructor(
    readonly provider: LlmProviderId,
    readonly kind: ProviderFailureKind,
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = 'ProviderCallError';
  }
}

export interface LlmProvider {
  readonly id: LlmProviderId;
  readonly configured: boolean;
  readonly model: string;
  /** Can this provider accept inline images (vision)? Requests carrying an
   *  image are only routed to providers where this is true (D-66). */
  readonly vision?: boolean;
  generate(req: GenerateRequest, signal: AbortSignal): Promise<ProviderResult>;
}

/** True when an error is an abort/cancellation (the combined signal fired: caller
 *  disconnect or our own timeout). Providers use this to raise `canceled` instead
 *  of `transient`, so a disconnect never trips the breaker (D-103). */
export function isAbortError(err: unknown, signal?: AbortSignal): boolean {
  if (signal?.aborted) return true;
  const name = (err as { name?: unknown })?.name;
  if (name === 'AbortError' || name === 'TimeoutError' || name === 'APIUserAbortError') return true;
  const msg = (err as { message?: unknown })?.message;
  return typeof msg === 'string' && /\babort|\bcancel/i.test(msg);
}

/** Classify an HTTP status into a breaker action. */
export function classifyStatus(provider: LlmProviderId, status: number, body: string): ProviderCallError {
  const snippet = body.slice(0, 300);
  // 413, or a 400 whose body signals the request exceeded a size/token/rate
  // budget (e.g. Groq free-tier "Request too large ... tokens per minute"), is
  // a capacity limit, NOT a code fault — treat it like a rate limit so the
  // breaker opens with a TTL and the chain fails over cleanly instead of
  // silently degrading to mock as a "transient" error.
  const looksOversized = /too large|reduce your|tokens per minute|context length|maximum context|request too large|payload/i.test(body);
  if (status === 413 || status === 429 || (status === 400 && looksOversized)) {
    return new ProviderCallError(provider, 'rate_limit', `${status} from ${provider}: ${snippet}`, status);
  }
  if (status === 401 || status === 402 || status === 403) {
    return new ProviderCallError(provider, 'auth_or_billing', `${status} from ${provider}: ${snippet}`, status);
  }
  return new ProviderCallError(provider, 'transient', `${status} from ${provider}: ${snippet}`, status);
}

/** Rough token estimate (~4 chars/token) for budgeting requests without a
 *  tokenizer dependency; intentionally conservative (rounds up). */
export function estimateTokens(text: string): number {
  return Math.ceil(text.length / 4);
}

/**
 * Cap the completion budget so `promptTokens + maxTokens` stays under a
 * provider's per-request token budget (e.g. Groq free tier's 12k TPM). Returns
 * the original request when no budget applies or it already fits. Never returns
 * below `floor` — a smaller budget than the prompt means the caller should let
 * the request fail over rather than send a doomed 1-token completion.
 */
export function fitMaxTokens(promptTokens: number, requestedMax: number, budget: number, floor = 512): number {
  if (budget <= 0) return requestedMax;
  const room = budget - promptTokens - 256; // 256-token safety margin
  if (room >= requestedMax) return requestedMax;
  return Math.max(floor, room);
}
