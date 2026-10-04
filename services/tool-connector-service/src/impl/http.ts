import { SdlcError } from '@sdlc/shared';

/**
 * Shared outbound HTTP helper for the live connectors: per-attempt timeout,
 * bounded retries with exponential backoff + jitter, and Retry-After support.
 *
 * Retry rules (the safety contract):
 *  - A rate-limited response (HTTP 429, or whatever `isRateLimited` says) means
 *    the request was NOT processed, so it is retried for every method.
 *  - 502/503/504 and network errors/timeouts are retried ONLY for idempotent
 *    requests, because a non-idempotent POST may already have taken effect.
 *  - Everything else (including 4xx) is returned to the caller untouched.
 *
 * The helper never throws for an HTTP status: after the final attempt the last
 * response is returned so callers can map it. It throws SdlcError(TOOL_ERROR)
 * only when no response could be obtained. Request headers (credentials) are
 * never included in error messages.
 */
export interface HttpPolicy {
  /** Per-attempt timeout (covers headers and body). */
  timeoutMs: number;
  /** Retries after the first attempt. */
  maxRetries: number;
  baseDelayMs: number;
  maxDelayMs: number;
  /** A Retry-After longer than this is not waited on; the response is returned instead. */
  maxRetryAfterMs: number;
  sleep: (ms: number) => Promise<void>;
  /** Uniform [0,1); injectable so tests are deterministic. */
  random: () => number;
  fetch: typeof fetch;
}

export const DEFAULT_HTTP_POLICY: HttpPolicy = {
  timeoutMs: 30_000,
  maxRetries: 4,
  baseDelayMs: 500,
  maxDelayMs: 20_000,
  maxRetryAfterMs: 60_000,
  sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  random: Math.random,
  fetch: (...args) => fetch(...args),
};

export function resolvePolicy(overrides?: Partial<HttpPolicy>): HttpPolicy {
  return { ...DEFAULT_HTTP_POLICY, ...(overrides ?? {}) };
}

export interface HttpRequest {
  url: string;
  method?: string;
  headers?: Record<string, string>;
  body?: string;
  /** Safe to repeat after an ambiguous failure. Defaults to true for GET/HEAD/PUT/DELETE/OPTIONS. */
  idempotent?: boolean;
  /** Short name used in error messages, e.g. "Jira" or "GitHub". */
  label: string;
}

export interface HttpResult {
  status: number;
  ok: boolean;
  headers: Headers;
  text(): Promise<string>;
  json<T = unknown>(): Promise<T>;
}

export type RateLimitDetector = (status: number, headers: Headers, body: string) => boolean;

export const defaultRateLimitDetector: RateLimitDetector = (status) => status === 429;

const IDEMPOTENT_METHODS = new Set(['GET', 'HEAD', 'PUT', 'DELETE', 'OPTIONS']);

/** Parse a Retry-After header (delta-seconds or HTTP-date) to milliseconds, or null. */
export function parseRetryAfterMs(value: string | null, now: number = Date.now()): number | null {
  if (value === null) return null;
  const trimmed = value.trim();
  if (trimmed === '') return null;
  if (/^\d+(\.\d+)?$/.test(trimmed)) return Math.round(Number(trimmed) * 1000);
  const at = Date.parse(trimmed);
  return Number.isNaN(at) ? null : Math.max(0, at - now);
}

/** Exponential backoff with equal jitter: delay in [d/2, d] where d = min(max, base * 2^attempt). */
export function backoffMs(attempt: number, policy: Pick<HttpPolicy, 'baseDelayMs' | 'maxDelayMs' | 'random'>): number {
  const ceiling = Math.min(policy.maxDelayMs, policy.baseDelayMs * 2 ** attempt);
  return Math.round(ceiling / 2 + (policy.random() * ceiling) / 2);
}

function buildResult(status: number, headers: Headers, bodyText: string): HttpResult {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers,
    async text() {
      return bodyText;
    },
    async json<T>() {
      try {
        return JSON.parse(bodyText) as T;
      } catch (cause) {
        throw new SdlcError('TOOL_ERROR', `Upstream returned a non-JSON body (HTTP ${status})`, { cause });
      }
    },
  };
}

export async function httpRequest(
  req: HttpRequest,
  policy: HttpPolicy,
  isRateLimited: RateLimitDetector = defaultRateLimitDetector,
): Promise<HttpResult> {
  const method = (req.method ?? 'GET').toUpperCase();
  const idempotent = req.idempotent ?? IDEMPOTENT_METHODS.has(method);
  let lastFailure = 'no response';

  for (let attempt = 0; ; attempt++) {
    const retriesLeft = attempt < policy.maxRetries;
    let result: HttpResult | undefined;
    let bodyText = '';
    try {
      const res = await policy.fetch(req.url, {
        method,
        headers: req.headers,
        body: req.body,
        signal: AbortSignal.timeout(policy.timeoutMs),
      });
      bodyText = await res.text();
      result = buildResult(res.status, res.headers, bodyText);
    } catch (err) {
      const timedOut = err instanceof Error && (err.name === 'TimeoutError' || err.name === 'AbortError');
      lastFailure = timedOut ? `timed out after ${policy.timeoutMs}ms` : err instanceof Error ? err.message : 'network error';
      if (idempotent && retriesLeft) {
        await policy.sleep(backoffMs(attempt, policy));
        continue;
      }
      throw new SdlcError('TOOL_ERROR', `${req.label} ${method} request failed: ${lastFailure}`, {
        cause: err,
        details: { attempts: attempt + 1 },
      });
    }

    const rateLimited = isRateLimited(result.status, result.headers, bodyText);
    const transient = idempotent && (result.status === 502 || result.status === 503 || result.status === 504);
    if (!(rateLimited || transient) || !retriesLeft) return result;

    let delay = backoffMs(attempt, policy);
    const retryAfter = parseRetryAfterMs(result.headers.get('retry-after'));
    if (retryAfter !== null) {
      if (retryAfter > policy.maxRetryAfterMs) return result; // not worth blocking a tool call on
      delay = retryAfter + Math.round(policy.random() * 100);
    } else if (rateLimited && result.headers.get('x-ratelimit-remaining') === '0') {
      // GitHub primary limit: wait for the window reset if it is close, else give up.
      const resetSeconds = Number(result.headers.get('x-ratelimit-reset'));
      if (Number.isFinite(resetSeconds) && resetSeconds > 0) {
        const wait = resetSeconds * 1000 - Date.now() + 1000;
        if (wait > policy.maxRetryAfterMs) return result;
        delay = Math.max(delay, wait);
      }
    }
    await policy.sleep(delay);
  }
}
