import { SdlcError } from '@sdlc/shared';
import type { ToolsEnv } from '@sdlc/shared';
import { httpRequest, resolvePolicy, type HttpPolicy, type HttpResult, type RateLimitDetector } from './http.js';

/**
 * GitHub primary/secondary rate limiting: 429, or 403 carrying Retry-After,
 * an exhausted x-ratelimit-remaining, or a rate-limit message. In all of these
 * the request was rejected, so it is safe to retry for any method.
 */
export const githubRateLimitDetector: RateLimitDetector = (status, headers, body) => {
  if (status === 429) return true;
  if (status !== 403) return false;
  return headers.has('retry-after') || headers.get('x-ratelimit-remaining') === '0' || /rate limit|abuse detection|secondary rate/i.test(body);
};

function messageOf(body: string): string {
  try {
    const parsed: unknown = JSON.parse(body);
    if (typeof parsed === 'object' && parsed !== null) {
      const rec = parsed as Record<string, unknown>;
      const msg = typeof rec['message'] === 'string' ? rec['message'] : '';
      const errors = Array.isArray(rec['errors'])
        ? rec['errors'].map((e) => (typeof e === 'string' ? e : typeof e === 'object' && e !== null && typeof (e as Record<string, unknown>)['message'] === 'string' ? String((e as Record<string, unknown>)['message']) : '')).filter((s) => s !== '')
        : [];
      return [msg, ...errors].filter((s) => s !== '').join('; ').slice(0, 500);
    }
  } catch {
    /* not JSON */
  }
  return body.replace(/\s+/g, ' ').slice(0, 300);
}

/** Map a non-success GitHub response onto the repo error taxonomy. Never includes credentials. */
export function mapGithubError(status: number, body: string, context: string): SdlcError {
  const detail = messageOf(body);
  const details = { status, context };
  if (status === 401) {
    return new SdlcError('TOOL_ERROR', `GitHub 401 on ${context}: authentication failed - check GITHUB_TOKEN (expired or revoked?)`, { details });
  }
  if (status === 429 || (status === 403 && /rate limit|abuse detection|secondary rate/i.test(detail))) {
    return new SdlcError('RATE_LIMITED', `GitHub rate limit on ${context} persisted after retries (${status})`, { details });
  }
  if (status === 403) {
    return new SdlcError('TOOL_ERROR', `GitHub 403 on ${context}: forbidden - the token lacks permission for this repository/operation${detail ? ` (${detail})` : ''}`, { details });
  }
  if (status === 404) return new SdlcError('NOT_FOUND', `GitHub ${context}: not found (404)${detail ? ` - ${detail}` : ''}`, { details });
  if (status === 409) return new SdlcError('GATE_CONFLICT', `GitHub conflict on ${context} (409): ${detail}`, { details });
  if (status === 422) return new SdlcError('VALIDATION_FAILED', `GitHub rejected ${context} (422): ${detail}`, { details });
  return new SdlcError('TOOL_ERROR', `GitHub ${status} on ${context}: ${detail}`, { details });
}

export interface GithubRequestInit {
  method?: string;
  body?: string;
  headers?: Record<string, string>;
  idempotent?: boolean;
}

export interface GithubClient {
  /** Retries per policy, never throws for an HTTP status. */
  raw(path: string, init?: GithubRequestInit): Promise<HttpResult>;
  /** Parse a success body or throw the mapped error. */
  ensure<T>(res: HttpResult, context: string): Promise<T>;
  /** JSON request: GET unless a body/method is given. */
  json<T>(method: string, path: string, context: string, opts?: { body?: unknown; idempotent?: boolean }): Promise<T>;
  /** `owner/name` of the configured repository (validated). */
  repo(): string;
  readonly policy: HttpPolicy;
}

const DEFAULT_GITHUB_RETRIES = 3;

export function githubClient(env: ToolsEnv, overrides?: Partial<HttpPolicy>): GithubClient {
  const policy = resolvePolicy({ maxRetries: DEFAULT_GITHUB_RETRIES, ...(overrides ?? {}) });
  const apiBase = env.GITHUB_API_URL.replace(/\/+$/, '');

  const client: GithubClient = {
    policy,
    repo() {
      const repo = env.GITHUB_REPO ?? '';
      if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo)) throw new SdlcError('INTERNAL', 'GITHUB_REPO must look like "owner/name"');
      return repo;
    },
    async raw(path, init = {}) {
      return httpRequest(
        {
          label: 'GitHub',
          url: `${apiBase}${path}`,
          method: init.method ?? 'GET',
          headers: {
            accept: 'application/vnd.github+json',
            authorization: `Bearer ${env.GITHUB_TOKEN ?? ''}`,
            'x-github-api-version': '2022-11-28',
            ...(init.body !== undefined ? { 'content-type': 'application/json' } : {}),
            ...(init.headers ?? {}),
          },
          ...(init.body !== undefined ? { body: init.body } : {}),
          ...(init.idempotent !== undefined ? { idempotent: init.idempotent } : {}),
        },
        policy,
        githubRateLimitDetector,
      );
    },
    async ensure<T>(res: HttpResult, context: string): Promise<T> {
      if (!res.ok) throw mapGithubError(res.status, await res.text(), context);
      return res.json<T>();
    },
    async json<T>(method: string, path: string, context: string, opts: { body?: unknown; idempotent?: boolean } = {}) {
      const res = await client.raw(path, {
        method,
        ...(opts.body !== undefined ? { body: JSON.stringify(opts.body) } : {}),
        ...(opts.idempotent !== undefined ? { idempotent: opts.idempotent } : {}),
      });
      return client.ensure<T>(res, context);
    },
  };
  return client;
}
