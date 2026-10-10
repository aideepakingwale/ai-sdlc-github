import type { AttemptRecord, GenerateRequest, LlmProviderId, LlmUsage } from '@sdlc/shared';

export interface ProviderResult {
  content: string;
  model: string;
  usage: LlmUsage;
  /** True when the provider stopped at the output-token cap (max_tokens /
   *  length / MAX_TOKENS) — the content is incomplete (D-103). */
  truncated?: boolean;
  /** Provider-side ids and context for token governance (kept per attempt). */
  providerRequestId?: string;
  stopReason?: string;
  region?: string;
}

/** Build a full usage record. `promptTokens` is always the TOTAL input
 *  (uncached + cache read + cache write); the split rides alongside it. */
export function makeUsage(
  input: number,
  output: number,
  extra: { cacheRead?: number; cacheWrite?: number; estimated?: boolean } = {},
): LlmUsage {
  const cacheRead = Math.max(0, Math.trunc(extra.cacheRead ?? 0));
  const cacheWrite = Math.max(0, Math.trunc(extra.cacheWrite ?? 0));
  const uncached = Math.max(0, Math.trunc(input));
  return {
    promptTokens: uncached + cacheRead + cacheWrite,
    completionTokens: Math.max(0, Math.trunc(output)),
    inputTokens: uncached,
    cacheReadTokens: cacheRead,
    cacheWriteTokens: cacheWrite,
    estimated: extra.estimated === true,
  };
}

export const ZERO_USAGE: LlmUsage = makeUsage(0, 0);

/** What a failed attempt still tells us (token governance): tokens the provider
 *  billed before the call failed, and the ids needed to find it in provider logs. */
export interface FailureInfo {
  usage?: LlmUsage;
  providerRequestId?: string;
  stopReason?: string;
  region?: string;
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
  /** Attached by the router when it rethrows, so the caller can still account for
   *  every attempt (and its tokens) that preceded the failure. */
  usageLog?: AttemptRecord[];

  constructor(
    readonly provider: LlmProviderId,
    readonly kind: ProviderFailureKind,
    message: string,
    readonly status?: number,
    /** Set by a provider when the failed call was still (partly) billed. */
    readonly info: FailureInfo = {},
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
  /** `onText`, when supplied, is invoked with each streamed text delta as the
   *  provider generates (D-112 live streaming). Providers that can't stream simply
   *  ignore it and return the full result; the caller then emits it as one chunk. */
  generate(req: GenerateRequest, signal: AbortSignal, onText?: (delta: string) => void): Promise<ProviderResult>;
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
